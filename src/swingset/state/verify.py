"""Re-derive a weekly sample of current link and year units and compare their output.

A unit is current when nothing it recorded reading has moved. If its read
set missed an input, a changed input leaves it current with stale output
(plan section 10, "Read-set tracking misses a read"). This check finds that
case. Each week (`week`) the maintain loop ranks every link and year scope by
a hash of the week and the scope, and verifies the first `SAMPLE` of each
kind that are current, at least one per kind.

To verify a unit, it runs the unit's real write (`process_unit` or
`link_event`) inside one write transaction, labels the live output the way
completion labels a generation, and rolls the whole write back. It then
compares that label with the `output_digest` and `row_count` of the
generation the scope's pointer names. A mismatch is a read-set miss. So is
a re-run that raises `NondeterministicOutputError`: the unit is current, so
its recorded inputs are the ones it ran on, and its output differs (F29).
It is listed in the summary under `mismatches` and appended to `ALERTS.log`
as `[read-set-miss]`, and the watchdog repeats it daily until a later sample
is clean. A unit that is not current is skipped; it is going to run anyway.
A unit whose write fails otherwise is listed under `failures`.

A sample is `complete` when every kind verified its target and no stop cut
it short. Only a complete sample makes the next one wait `INTERVAL`
(`due`), so a sample cut short, or one whose units were mostly stale, runs
again at the next maintain tick. The summary also carries the process's peak
resident memory and the longest unit, which holds the writer lock for its
whole write.
"""

from __future__ import annotations

import hashlib
import math
import resource
import sqlite3
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from swingset.clock import Clock
from swingset.log import log

from .db import Database
from .work import WorkUnit

if TYPE_CHECKING:
    from swingset.state.inputs import InputBundle

#: The sampled kinds, by their `--kinds` name.
KINDS: Mapping[str, tuple[str, str]] = {"link": ("link", "event"), "year": ("project", "year")}
#: The share of each kind's current units verified each week.
SAMPLE = 0.01
#: The maintain loop verifies again once the newest verify is this old. The
#: loop runs daily at a fixed time, so the slack keeps the interval at seven
#: days instead of eight.
INTERVAL = timedelta(days=6, hours=12)
#: At most this many units are checked for currency per unit verified, so a
#: drain that leaves most units stale does not turn the step into a scan.
EXAMINE_FACTOR = 20
#: The key of the ALERTS.log line; the watchdog repeats it (nix/watchdog.py).
ALERT_KEY = "read-set-miss"


class _RollBack(Exception):
    """Raised after the label is read, so the unit's write never commits."""


def week(now: datetime) -> str:
    """The ISO week that seeds the sample, such as `2026-W40`."""
    year, number, _ = now.astimezone(UTC).isocalendar()
    return f"{year}-W{number:02d}"


def ranked(conn: sqlite3.Connection, stage: str, kind: str, *, seed: str) -> list[WorkUnit]:
    """Every scope of one kind with a pointer, in the week's sample order."""
    units = [
        WorkUnit(stage, kind, str(unit_id))
        for (unit_id,) in conn.execute(
            "SELECT unit_id FROM derivation_scopes WHERE stage=? AND unit_kind=? "
            "AND materialized_generation_id IS NOT NULL",
            (stage, kind),
        )
    ]
    return sorted(units, key=lambda unit: _rank(seed, unit))


def _rank(seed: str, unit: WorkUnit) -> str:
    return hashlib.sha256(
        f"{seed}|{unit.stage}/{unit.unit_kind}/{unit.unit_id}".encode()
    ).hexdigest()


def target(count: int, fraction: float) -> int:
    """How many units of a kind with `count` scopes a sample verifies."""
    if count <= 0 or fraction <= 0:
        return 0
    return min(count, max(1, math.ceil(count * fraction)))


def due(conn: sqlite3.Connection, *, now: datetime) -> bool:
    """Whether no maintain tick recorded a complete verify within `INTERVAL`.

    A sample cut short or short of a kind's target does not count: the next
    maintain tick samples again (I4).
    """
    since = (now - INTERVAL).astimezone(UTC).isoformat()
    row = conn.execute(
        "SELECT 1 FROM runs WHERE loop='maintain' AND finished_at > ? "
        "AND json_valid(summary_json) AND json_extract(summary_json,'$.verify.week') IS NOT NULL "
        "AND json_extract(summary_json,'$.verify.complete') = 1 LIMIT 1",
        (since,),
    ).fetchone()
    return row is None


def _pointer_label(conn: sqlite3.Connection, unit: WorkUnit) -> tuple[str, int] | None:
    row = conn.execute(
        "SELECT g.output_digest,g.row_count FROM derivation_scopes s "
        "JOIN derivation_generations g ON g.generation_id=s.materialized_generation_id "
        "WHERE s.stage=? AND s.unit_kind=? AND s.unit_id=?",
        (unit.stage, unit.unit_kind, unit.unit_id),
    ).fetchone()
    return None if row is None else (str(row[0]), int(row[1]))


def _live_label(conn: sqlite3.Connection, unit: WorkUnit) -> tuple[str, int]:
    """The live output's label, computed as completion computes a generation's."""
    from swingset.project.materialization import output_rows

    # The same two steps `complete` takes, so the labels compare exactly.
    from .derivations import _fingerprint, _offered

    return _fingerprint(_offered(output_rows(conn, unit)))


def _cutoff_on(bundle: InputBundle) -> bool:
    config = getattr(bundle, "config", None)
    derivation = getattr(config, "derivation", None)
    return True if derivation is None else bool(derivation.cutoff)


def verify_unit(
    database: Database,
    unit: WorkUnit,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
) -> dict[str, Any]:
    """Run one unit's write, label its output, roll it back and compare.

    Returns `state`: `match`, `mismatch`, `superseded` (its inputs moved
    during the write, so it was not current) or `failed`. `expected` is the
    pointer's label, `live` the output before the write, `found` after it.
    A re-run that raises `NondeterministicOutputError` is a `mismatch` with
    `found` None and the error: its inputs are the recorded ones, so the
    output it would not store differs from the generation's.
    """
    from swingset import cutoff
    from swingset.link import link_event
    from swingset.project.materialization import helper_recipe
    from swingset.project.process import process_unit

    from . import derivations
    from .attempts import SupersededWorkError

    labels: dict[str, Any] = {}
    name = f"{unit.stage}/{unit.unit_kind}/{unit.unit_id}"
    try:
        with cutoff.configured(_cutoff_on(bundle)), database.transaction() as conn:
            labels["expected"] = _pointer_label(conn, unit)
            labels["live"] = _live_label(conn, unit)
            # A generation names its run; this one is rolled back with it.
            conn.execute(
                "INSERT OR IGNORE INTO runs(run_id,started_at,dry_run) VALUES (?,?,1)",
                (run_id, clock.now().isoformat()),
            )
            if unit.stage == "link":
                # As derive passes one: a supersession then raises, not returns.
                selection = derivations.capture(
                    conn, unit, now=clock.now(), recipe=helper_recipe(conn, bundle.files, "link")
                )
                link_event(database, unit.unit_id, bundle, clock, run_id, selection=selection)
            else:
                process_unit(database, unit, bundle, clock, run_id)
            labels["found"] = _live_label(conn, unit)
            raise _RollBack
    except _RollBack:
        pass
    except SupersededWorkError as exc:
        return {"unit": name, "state": "superseded", "error": str(exc)[:200]}
    except derivations.NondeterministicOutputError as exc:
        expected = labels.get("expected")
        live = labels.get("live")
        return {
            "unit": name,
            "state": "mismatch",
            "reason": "nondeterministic_output",
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "expected": None if expected is None else list(expected),
            "live": None if live is None else list(live),
            "found": None,
        }
    except Exception as exc:  # one unit must not stop the sample
        return {"unit": name, "state": "failed", "error": f"{type(exc).__name__}: {exc}"[:300]}
    expected, found = labels["expected"], labels["found"]
    return {
        "unit": name,
        "state": "match"
        if expected is not None and tuple(found) == tuple(expected)
        else "mismatch",
        "expected": None if expected is None else list(expected),
        "live": list(labels["live"]),
        "found": list(found),
    }


def run(
    database: Database,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    *,
    fraction: float = SAMPLE,
    kinds: Iterable[str] = tuple(KINDS),
    stop: Callable[[], bool] = lambda: False,
    alert: bool = True,
) -> dict[str, Any]:
    """Verify this week's sample; the summary the maintain tick and the CLI report.

    Each unit is its own write, rolled back. A mismatch is logged
    (`read_set_miss`) and, with `alert`, appended to `ALERTS.log`.
    """
    from . import derivations
    from .recipes import RecipeScopeMissing

    started = time.monotonic()
    now = clock.now()
    seed = week(now)
    summary: dict[str, Any] = {
        "week": seed,
        "fraction": fraction,
        "kinds": {},
        "mismatches": [],
        "failures": [],
        "stopped": False,
        "complete": False,
        "longest_unit_seconds": 0.0,
    }
    conn = database.connection
    if not derivations.available(conn):
        summary["skipped"] = "no derivations"
        return summary
    for name in kinds:
        stage, kind = KINDS[name]
        units = ranked(conn, stage, kind, seed=seed)
        wanted = target(len(units), fraction)
        counts = {
            "units": len(units),
            "target": wanted,
            "verified": 0,
            "matched": 0,
            "not_current": 0,
            "superseded": 0,
            "failed": 0,
        }
        summary["kinds"][name] = counts
        for examined, unit in enumerate(units):
            if counts["verified"] >= wanted or examined >= wanted * EXAMINE_FACTOR:
                break
            if stop():
                summary["stopped"] = True
                break
            try:
                current = derivations.current(conn, unit)
            except RecipeScopeMissing:
                current = False
            if not current:
                counts["not_current"] += 1
                continue
            began = time.monotonic()
            result = verify_unit(database, unit, bundle, clock, run_id)
            summary["longest_unit_seconds"] = max(
                summary["longest_unit_seconds"], round(time.monotonic() - began, 3)
            )
            state = result["state"]
            if state == "superseded":
                counts["superseded"] += 1
                continue
            if state == "failed":
                counts["failed"] += 1
                summary["failures"].append(result)
                continue
            counts["verified"] += 1
            if state == "match":
                counts["matched"] += 1
            else:
                summary["mismatches"].append(result)
                log("read_set_miss", **result)
        if summary["stopped"]:
            break
    summary["complete"] = not summary["stopped"] and all(
        counts["verified"] >= counts["target"] for counts in summary["kinds"].values()
    )
    summary["seconds"] = round(time.monotonic() - started, 3)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    summary["peak_rss_bytes"] = int(peak if sys.platform == "darwin" else peak * 1024)
    if alert and summary["mismatches"]:
        summary["alerted"] = append_alerts(database.state_dir, summary["mismatches"], now=now)
    return summary


def alert_lines(mismatches: Sequence[Mapping[str, Any]]) -> list[str]:
    """One `[read-set-miss]` line per unit, as `append_alerts` writes it."""
    return [
        f"[{ALERT_KEY}] {item['unit']}: pointer {_label(item.get('expected'))},"
        f" re-derived {_label(item.get('found'))}"
        + (f" ({item['reason']})" if item.get("reason") else "")
        + " (verify sample)"
        for item in mismatches
    ]


def _label(value: Any) -> str:
    if not value:
        return "none"
    return f"{str(value[0])[:12]} ({int(value[1])} rows)"


def append_alerts(
    state_dir: Path, mismatches: Sequence[Mapping[str, Any]], *, now: datetime
) -> bool:
    """Append the lines to `ALERTS.log` with the watchdog's stamp; False when it cannot.

    The watchdog also sends a `[read-set-miss]` line of its own, with other
    text, so `alertCommand` hears of it: this file may not be writable by
    the service user.
    """
    stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(state_dir / "ALERTS.log", "a") as handle:
            for line in alert_lines(mismatches):
                handle.write(f"{stamp} {line}\n")
    except OSError as exc:
        log("read_set_miss_alert_failed", error=f"{type(exc).__name__}: {exc}"[:200])
        return False
    return True


def parse_kinds(value: str) -> tuple[str, ...]:
    """`link,year` as a tuple; an unknown name raises ValueError."""
    names = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    unknown = [name for name in names if name not in KINDS]
    if unknown or not names:
        raise ValueError(f"--kinds takes a comma-separated subset of {', '.join(KINDS)}")
    return names


__all__ = [
    "ALERT_KEY",
    "INTERVAL",
    "KINDS",
    "SAMPLE",
    "alert_lines",
    "append_alerts",
    "due",
    "parse_kinds",
    "ranked",
    "run",
    "target",
    "verify_unit",
    "week",
]
