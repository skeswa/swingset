"""Closed acquisition waiting intervals and conservative local alarm decisions."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from swingset.clock import Clock
from swingset.fetch.eligibility import Assessment
from swingset.state.db import Database


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def same_dependencies(previous: str, current: str) -> bool:
    """New evidence needs rechecking, but failure cannot erase waiting clocks."""
    if previous == current:
        return True
    try:
        before, after = json.loads(previous), json.loads(current)
        return {key: value for key, value in before.items() if key != "revision"} == {
            key: value for key, value in after.items() if key != "revision"
        }
    except (ValueError, TypeError, AttributeError):
        return False


@dataclass(frozen=True)
class Boundary:
    wall: datetime
    elapsed: float
    revision: int
    marker: tuple[int, int, bool]
    token: str


def sample(database: Database, clock: Clock, token: str) -> Boundary:
    from swingset.state.control_lock import control_lock

    # Controls bypass the ordinary writer lock. Sample their commit boundary
    # under the same short lifecycle lock, never across network or sleep.
    with control_lock(database.state_dir):
        wall, elapsed = clock.now(), clock.monotonic()
        parent = database.state_dir.stat()
        marker = database.state_dir / "operator-hold"
        revision = database.connection.execute("SELECT revision FROM control_state").fetchone()[0]
        return Boundary(
            wall,
            elapsed,
            int(revision),
            (parent.st_mtime_ns, parent.st_ino, marker.exists()),
            token,
        )


class Recorder:
    """Persist closed intervals only; a process-local boundary is never restored."""

    def __init__(
        self,
        database: Database,
        *,
        source: str,
        source_ref: str,
        run_id: str,
        token: str,
        policy: dict[str, Any],
        at: Boundary,
    ) -> None:
        self.database = database
        self.start: Boundary | None = None
        self.assessment: Assessment | None = None
        self.suspended_at: Boundary | None = None
        previous = database.connection.execute(
            "SELECT * FROM event_timing WHERE source=? AND source_ref=?", (source, source_ref)
        ).fetchone()
        if (
            previous is not None
            and same_dependencies(previous["token_json"], token)
            and previous["policy_json"] == encoded(policy)
        ):
            self.row = dict(previous)
            gap = max(0.0, (at.wall - datetime.fromisoformat(self.row["saved_at"])).total_seconds())
            if self.row["phase_open"] or self.row["run_id"] != run_id:
                self.row["unknown_seconds"] += gap
                self.row["complete_coverage"] = 0
                self.row["reason"] = "interrupted_or_unobserved_phase"
            else:
                self.row["inactive_seconds"] += gap
            self.row["run_id"] = run_id
            self.row["token_json"] = token
        else:
            self.row = dict(
                source=source,
                source_ref=source_ref,
                episode_id="timing_" + uuid4().hex,
                token_json=token,
                policy_json=encoded(policy),
                observation_origin=at.wall.isoformat(),
                run_id=run_id,
                eligible_seconds=0.0,
                blocked_seconds=0.0,
                unknown_seconds=0.0,
                inactive_seconds=0.0,
                since_service_seconds=0.0,
                since_progress_seconds=0.0,
                complete_coverage=1,
                service_bound_valid=1,
                progress_bound_valid=1,
                reason_seconds_json="{}",
                service_frontier=0,
                progress_frontier=0,
                last_progress_at=None,
                last_service_action=None,
                last_service_at=None,
                unresolved_success=0,
                operation_origin=database.connection.execute(
                    "SELECT coalesce(max(operation_id),0) FROM event_stage_operations"
                ).fetchone()[0],
            )
            if previous is not None:
                # Changing the measurement episode never manufactures progress.
                # Keep waiting counters as lower bounds until a new receipt
                # resets its own clock; old receipts cannot reset them again.
                for key in (
                    "since_service_seconds",
                    "since_progress_seconds",
                    "service_frontier",
                    "progress_frontier",
                    "last_progress_at",
                    "operation_origin",
                    "last_service_action",
                    "last_service_at",
                ):
                    self.row[key] = previous[key]
                self.row["complete_coverage"] = 0
                self.row["service_bound_valid"] = self.row["progress_bound_valid"] = 0
        self.row.update(
            saved_at=at.wall.isoformat(),
            phase_open=1,
            state="unknown",
            reason="phase_assessment_pending",
            valid_until=at.wall.isoformat(),
            control_revision=at.revision,
            marker_mtime_ns=at.marker[0],
        )
        self._save()

    def _save(self) -> None:
        row = self.row
        columns = list(row)
        with self.database.transaction() as conn:
            conn.execute(
                "INSERT INTO event_timing("
                + ",".join(columns)
                + ") VALUES("
                + ",".join("?" for _ in columns)
                + ") "
                "ON CONFLICT(source,source_ref) DO UPDATE SET "
                + ",".join(
                    key + "=excluded." + key
                    for key in columns
                    if key not in {"source", "source_ref"}
                ),
                tuple(row[key] for key in columns),
            )
            conn.execute(
                "INSERT INTO event_timing_history VALUES(?,?,?,?,?) ON CONFLICT(episode_id,run_id) "
                "DO UPDATE SET summary_json=excluded.summary_json",
                (row["episode_id"], row["run_id"], row["source"], row["source_ref"], encoded(row)),
            )

    def open(
        self,
        at: Boundary,
        assessment: Assessment,
        *,
        service: int = 0,
        progress: int = 0,
        progress_at: str | None = None,
        unresolved_success: bool = False,
        service_action: str | None = None,
        service_at: str | None = None,
    ) -> None:
        if self.start is not None:
            raise ValueError("close the preceding timing interval before reopening")
        row = self.row
        if at.marker[2]:
            assessment = Assessment("blocked", "operator_hold", assessment.until)
        if self.suspended_at is not None:
            gap = max(0.0, at.elapsed - self.suspended_at.elapsed)
            if gap:
                row["unknown_seconds"] += gap
                row["complete_coverage"] = 0
        self.suspended_at = None
        self.start, self.assessment = at, assessment
        if service > row["service_frontier"]:
            row["since_service_seconds"] = 0.0
            row["service_bound_valid"] = 1
            row["last_service_action"], row["last_service_at"] = service_action, service_at
        if progress > row["progress_frontier"]:
            row["since_progress_seconds"] = 0.0
            row["progress_bound_valid"] = 1
            row["last_progress_at"] = progress_at
        row.update(
            service_frontier=max(service, row["service_frontier"]),
            progress_frontier=max(progress, row["progress_frontier"]),
            unresolved_success=int(unresolved_success),
            state=assessment.state,
            reason=assessment.reason,
            valid_until=assessment.until.isoformat(),
            phase_open=1,
            saved_at=at.wall.isoformat(),
            control_revision=at.revision,
            marker_mtime_ns=at.marker[0],
        )
        self._save()

    def close(self, at: Boundary, *, inactive: bool = False) -> None:
        row = self.row
        if self.start is None and self.suspended_at is not None:
            if not inactive:
                return
            gap = max(0.0, at.elapsed - self.suspended_at.elapsed)
            if gap:
                row["unknown_seconds"] += gap
                row["complete_coverage"] = 0
        if self.start is not None:
            start, assessment = self.start, self.assessment
            assert assessment is not None
            elapsed = max(0.0, at.elapsed - start.elapsed)
            wall = (at.wall - start.wall).total_seconds()
            consistent = (
                math.isfinite(elapsed)
                and abs(elapsed - wall) <= 0.25
                and start.revision == at.revision
                and start.marker == at.marker
                and start.token == at.token
            )
            known = (
                min(elapsed, max(0.0, (assessment.until - start.wall).total_seconds()))
                if consistent
                else 0.0
            )
            if assessment.state == "unknown":
                known = 0.0
            if known:
                row[assessment.state + "_seconds"] += known
                reasons = json.loads(row["reason_seconds_json"])
                reasons[assessment.reason] = reasons.get(assessment.reason, 0.0) + known
                row["reason_seconds_json"] = encoded(reasons)
                if assessment.state == "eligible":
                    row["since_service_seconds"] += known
                    row["since_progress_seconds"] += known
            unknown = elapsed - known
            if unknown:
                row["unknown_seconds"] += unknown
                row["complete_coverage"] = 0
                row["reason"] = "interval_proof_changed_or_expired"
                row["state"] = "unknown"
        self.start = None
        self.assessment = None
        self.suspended_at = None if inactive else at
        row.update(saved_at=at.wall.isoformat(), phase_open=int(not inactive))
        if inactive:
            row.update(
                state="inactive", reason="acquisition_phase_closed", valid_until=at.wall.isoformat()
            )
        self._save()


def alarms(row: dict[str, Any], *, now: datetime, operator_hold: bool) -> dict[str, Any]:
    """Read closed counters; neither reporting nor failures advance/reset them."""
    policy = json.loads(row["policy_json"])
    state = row["state"]
    if operator_hold:
        state = "suppressed_by_hold"
    elif state != "inactive" and datetime.fromisoformat(row["valid_until"]) <= now:
        state = "unknown"
    result = {}
    for kind, counter in (
        ("service_gap", "since_service_seconds"),
        ("no_successful_progress", "since_progress_seconds"),
    ):
        threshold = policy.get(kind)
        verdict = state
        bound = row["service_bound_valid" if kind == "service_gap" else "progress_bound_valid"]
        if (
            not bound or (kind == "no_successful_progress" and row["unresolved_success"])
        ) and state != "suppressed_by_hold":
            verdict = "unknown"
        assessment = verdict
        if verdict == "eligible":
            if threshold is None:
                verdict = assessment = "unconfigured"
            elif row[counter] >= threshold:
                verdict = "alert"
                assessment = (
                    "threshold_exceeded" if row["complete_coverage"] else "lower_bound_exceeded"
                )
            else:
                verdict = "ok" if row["complete_coverage"] else "unknown"
                assessment = (
                    "below_threshold" if row["complete_coverage"] else "incomplete_coverage"
                )
        result[kind] = {
            "state": verdict,
            "assessment": assessment,
            "age_basis": "exact_observed_episode"
            if row["complete_coverage"]
            else "closed_eligible_lower_bound",
            "eligible_seconds": row[counter],
            "objective_seconds": threshold,
            "requirement": f"acquisition:{row['source']}:{row['source_ref']}",
            "last_qualified_progress_at": row["last_progress_at"],
            "guard_or_reason": row["reason"],
            "last_remedy": {
                "kind": "issued_request",
                "action_id": row["last_service_action"],
                "issued_at": row["last_service_at"],
            }
            if row["last_service_action"]
            else None,
            "next_action": "resume through ordinary request gates; inspect event blockers",
        }
    return result


def report(
    conn: sqlite3.Connection, *, source: str, source_ref: str, now: datetime, operator_hold: bool
) -> dict[str, Any]:
    supported = (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='event_timing'").fetchone() is not None
    )
    row = (
        conn.execute(
            "SELECT * FROM event_timing WHERE source=? AND source_ref=?", (source, source_ref)
        ).fetchone()
        if supported
        else None
    )
    result: dict[str, Any] = {
        "supported": supported,
        "scope": "observed acquisition phases only",
        "eligible_service_age_seconds": None,
        "legacy_eligible_age": "unknown",
        "whole_event_eligible_age": "unknown",
        "fleet_coverage": "bounded_rotating_cohort",
        "snapshot_at": now.isoformat(),
        "observation": dict(row) if row else None,
    }
    if row:
        from swingset.state.controls import ActionScope, matching_pauses

        from .event_pressure import token

        assessed = dict(row)
        reason = None
        try:
            old = json.loads(row["token_json"])
            if old != token(conn, source, source_ref, old["policy_digest"]):
                reason = "observation_dependencies_changed"
        except (ValueError, KeyError, TypeError):
            reason = "observation_dependencies_unverifiable"
        revision = conn.execute("SELECT revision FROM control_state").fetchone()[0]
        if revision != row["control_revision"]:
            reason = "control_revision_changed"
        if reason:
            assessed.update(state="unknown", reason=reason, complete_coverage=0)
        held = bool(matching_pauses(conn, ActionScope(sources=frozenset({source})), now=now))
        result["alarms"] = alarms(assessed, now=now, operator_hold=operator_hold or held)
        result["alarm_basis"] = "closed intervals; current time does not accrue additional waiting"

        result["wall_age_since_observation_seconds"] = max(
            0.0, (now - datetime.fromisoformat(row["observation_origin"])).total_seconds()
        )
        result["eligible_waiting_seconds_lower_bound"] = row["eligible_seconds"]
        result["exact_observed_active_eligible_seconds"] = (
            row["eligible_seconds"] if row["complete_coverage"] else None
        )
    return result
