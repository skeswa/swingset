"""The offline loop: derive runnable units in batches, for the cycle and derive.

Both callers own a run summary; this loop adds to the keys it is given
(`readiness_errors`, `unit_failures`, `stages`, `waiting_repeats`,
`control_timeouts`, `queue.lease_busy`, `queue.quarantined`) and to the
optional `current_hints_dropped`, `current_hints_error` and `not_completed`
(unfailed outcomes that committed no output, by reason), so the cycle's
summary is what it was before the loop moved here.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from swingset.clock import Clock
from swingset.fetch.archive import Archive
from swingset.log import log
from swingset.schedule import pacing
from swingset.schedule.derive import DerivationOutcome, derive_one
from swingset.schedule.fairness import next_offline_batch, record_offline_service
from swingset.schedule.registry import advance_sweep, run_saved_crosscheck_if_due
from swingset.state import derivations, queue
from swingset.state.attempts import latest_attempt
from swingset.state.control_scopes import for_unit
from swingset.state.controls import ControlPaused, operation
from swingset.state.db import Database
from swingset.state.inputs import InputBundle
from swingset.state.work import WorkUnit, unfinished_units
from swingset.state.work_fingerprints import input_fingerprint
from swingset.state.write_deadline import WriteDeadlineExceeded

# Units selected per read snapshot in the offline phase (D-0182).
OFFLINE_BATCH = 64

#: Outcomes that ran nothing: the unit is skipped for the rest of the run.
#: `process_interrupted`: a takeover counted a dead attempt of the unit, which
#: now waits on that failure's clock.
_NOT_ADMITTED = frozenset(
    {
        "control_timeout",
        "lease_busy",
        queue.FENCED_REASON,
        queue.LOCK_REASON,
        queue.INTERRUPTED_REASON,
    }
)


@dataclass
class Offline:
    """One run's offline state: what it tried, and its parse failures by source."""

    database: Database
    archive: Archive
    bundle: InputBundle
    clock: Clock
    run_id: str
    summary: dict[str, Any]
    allowed: Callable[[WorkUnit], bool]
    #: Only these stages run (derive's parse-only tick); None runs all.
    stages: frozenset[str] | None = None
    attempted_inputs: dict[WorkUnit, set[str]] = field(default_factory=dict)
    attempts: Counter[str] = field(default_factory=Counter)
    failures: Counter[str] = field(default_factory=Counter)
    #: Units whose attempt committed its output.
    completed: int = 0

    def offline_allowed(self, unit: WorkUnit) -> bool:
        if self.stages is not None and unit.stage not in self.stages:
            return False
        if not self.allowed(unit):
            return False
        conn = self.database.connection
        if pacing.deferred(conn, self.bundle.config.scheduler, unit, now=self.clock.now()):
            return False
        previous = self.attempted_inputs.get(unit)
        return previous is None or input_fingerprint(conn, unit) not in previous

    def batch(self) -> list[WorkUnit]:
        # A project or link hint whose unit is already current (a revert
        # deploy) is deleted once the selection's read snapshot closes;
        # otherwise the watchdog counts it as ready work forever (I4).
        found: dict[WorkUnit, str] = {}
        batch = next_offline_batch(
            self.database.connection,
            now=self.clock.now(),
            allowed=self.offline_allowed,
            limit=OFFLINE_BATCH,
            errors=self.summary["readiness_errors"],
            current_hints=found,
        )
        # Housekeeping: a failed delete is noted and retried next batch,
        # never fatal to the run.
        try:
            dropped = derivations.drop_current_hints(self.database, found)
        except (sqlite3.Error, WriteDeadlineExceeded) as exc:
            self.summary["current_hints_error"] = f"{type(exc).__name__}: {exc}"[:200]
            dropped = 0
        if dropped:
            self.summary["current_hints_dropped"] = (
                self.summary.get("current_hints_dropped", 0) + dropped
            )
        return batch

    def run(self, stop: Callable[[], bool]) -> None:
        """Derive batches until `stop()` or nothing is runnable."""
        while not stop() and (batch := self.batch()):
            for unit in batch:
                if stop():
                    break
                self.derive(unit)

    def _skip(self, unit: WorkUnit) -> None:
        """Leave ``unit`` alone for the rest of this run, whatever its inputs read."""
        try:
            fingerprint = input_fingerprint(self.database.connection, unit)
        except Exception:
            fingerprint = ""
        self.attempted_inputs.setdefault(unit, set()).add(fingerprint)

    def derive(self, unit: WorkUnit) -> DerivationOutcome:
        database, summary = self.database, self.summary

        def served(conn: sqlite3.Connection) -> None:
            # Rotated on admission, so a unit that kills its process does not
            # keep its kind group first on the next tick.
            record_offline_service(conn, unit, now=self.clock.now())

        try:
            attempt = derive_one(
                database, self.archive, unit, self.bundle, self.clock, self.run_id, on_admit=served
            )
        except Exception as exc:
            # Whatever escaped the unit stops only the unit (I1), and counts
            # as its failure, so it backs off and quarantines (I3). A fenced
            # or busy write leaves the attempt for the next tick, uncounted.
            self._skip(unit)
            try:
                with database.transaction():
                    failure = queue.escaped(database, unit, self.run_id, exc, now=self.clock.now())
                if failure is not None and failure.quarantined:
                    summary["queue"]["quarantined"].append(
                        f"{unit.stage}/{unit.unit_kind}/{unit.unit_id}"
                    )
            except (sqlite3.Error, WriteDeadlineExceeded):
                pass
            summary["unit_failures"].append(
                {
                    "stage": unit.stage,
                    "unit_kind": unit.unit_kind,
                    "unit_id": unit.unit_id,
                    "reason": f"escaped:{type(exc).__name__}",
                }
            )
            log(
                "unit_escaped",
                unit=f"{unit.stage}/{unit.unit_kind}/{unit.unit_id}",
                error=str(exc)[:300],
            )
            return DerivationOutcome(failed=True, reason="unit_exception")
        if attempt.reason == "operator_pause":
            return attempt
        if attempt.reason in _NOT_ADMITTED:
            # Nothing ran: skip the unit for this run.
            if attempt.reason == "control_timeout":
                summary["control_timeouts"] += 1
            elif attempt.reason == "lease_busy":
                summary["queue"]["lease_busy"] += 1
            else:
                held = summary["queue"].setdefault("held", {})
                held[attempt.reason] = held.get(attempt.reason, 0) + 1
            if attempt.quarantined:
                summary["queue"]["quarantined"].append(
                    f"{unit.stage}/{unit.unit_kind}/{unit.unit_id}"
                )
            self._skip(unit)
            return attempt
        if attempt.waiting_repeat:
            summary["waiting_repeats"] += 1
        if attempt.quarantined:
            summary["queue"]["quarantined"].append(f"{unit.stage}/{unit.unit_kind}/{unit.unit_id}")
        completed = latest_attempt(database.connection, unit)
        if completed is None:
            # The admission failed before an attempt began (`derive_one`).
            self._skip(unit)
        else:
            self.attempted_inputs.setdefault(unit, set()).add(str(completed["input_fingerprint"]))
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
        elif attempt.reason is None:
            # Progress is output committed. A supersession, a spurious one on
            # the queue's clock included, and a waiting repeat are counted
            # under their reasons instead, so they never hide a stall.
            self.completed += 1
        else:
            others = summary.setdefault("not_completed", {})
            others[attempt.reason] = others.get(attempt.reason, 0) + 1
        if unit.stage == "parse" and attempt.source is not None and not attempt.waiting_repeat:
            self.attempts[attempt.source] += 1
            self.failures[attempt.source] += int(attempt.failed)
            if attempt.source == "wsdc_registry":
                try:
                    with database.transaction():
                        advance_sweep(database, now=self.clock.now())
                except sqlite3.DatabaseError as exc:
                    # A publication fence or a busy writer: the sweep advances next time.
                    summary["registry_sweep_error"] = str(exc)[:200]
        return attempt

    def failed(self) -> bool:
        """More than a tenth of one source's parses failed, or any other unit did.

        A waiting unit's repeat is not counted (D-0216).
        """
        return any(
            self.failures[source] / count > 0.1 for source, count in self.attempts.items()
        ) or any(item["stage"] != "parse" for item in self.summary["unit_failures"])


def registry_crosscheck(
    database: Database,
    archive: Archive,
    clock: Clock,
    run_id: str,
    *,
    held: Callable[[str, ControlPaused], None],
    run: Callable[..., Any] = run_saved_crosscheck_if_due,
) -> bool:
    """Run a due saved registry crosscheck once no parse or project unit waits.

    Returns whether it was attempted. `run` lets the cycle pass its own name.
    """
    if next(iter(unfinished_units(database.connection, ("parse", "project"))), None) is not None:
        return False
    if not database.connection.execute(
        "SELECT 1 FROM meta WHERE key='registry_crosscheck_due'"
    ).fetchone():
        return False
    try:
        with operation(
            database,
            action_id="crosscheck_" + uuid4().hex,
            action_kind="registry_crosscheck",
            scope=for_unit(database.connection, WorkUnit("project", "history", "all")),
            clock=clock,
            run_id=run_id,
        ):
            run(database, archive, clock.now(), run_id)
    except ControlPaused as exc:
        held("registry_crosscheck", exc)
    return True
