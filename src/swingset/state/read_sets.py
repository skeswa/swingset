"""Record which canonical tables a project or link unit reads (plan section 9).

`recording(conn, unit)` installs a SQLite authorizer for the unit's body. The
authorizer runs when SQLite prepares a statement; each column or table read
of a canonical table is added to the recorder. It never denies a read.
Reads issued by derivation infrastructure (`INFRASTRUCTURE`: capture,
currentness, completion) are left out; they compute the unit's declared
dependencies. `record(conn, key)` adds an explicit read key for a later read
set.

SQLite authorizes a statement when it prepares it, and installing an
authorizer makes every cached statement prepare again. So each recording
sees every statement its body runs, except one that infrastructure prepared
first with the same text in the same recording.

`RULES` says, for each unit kind, which declared dependency covers each
canonical table it reads. A read table without a rule is an undeclared read:
it is logged (`read_undeclared`) and counted for the run summary, and
production never fails on it (I1).

Armed mode (`SWINGSET_READS_ARMED=1`, tests only) also raises
`UnrecordedRead`: for an undeclared table at unit end, for a canonical read by
unit-body code outside any recording (`guarding` marks the entry point), and
for `record` outside a recording. It also closes the authorizer's one blind
spot: SQLite does not report a table joined by `USING` or `NATURAL` whose
other columns go unread. Armed mode traces each such statement and adds the
tables its query plan opens. `SWINGSET_READS_REPORT=<file>` appends one JSON
line per unit and per violation.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import FrameType

from swingset.log import log
from swingset.model.schema import TABLES

from .work import WorkUnit

__all__ = [
    "CANONICAL",
    "EFFECT_ONLY",
    "INFRASTRUCTURE",
    "NESTED",
    "OWN_OUTPUT",
    "PENDING",
    "RULES",
    "Recorder",
    "UnrecordedRead",
    "active",
    "armed",
    "drain",
    "guarding",
    "pending",
    "record",
    "recording",
    "set_authorizer",
    "undeclared",
]

Authorizer = Callable[[int, str | None, str | None, str | None, str | None], int]

#: Tables whose rows are pipeline facts. A unit's output depends on what it
#: reads from these; control, queue and derivation bookkeeping are not here.
CANONICAL: frozenset[str] = frozenset(TABLES) | frozenset(
    {
        "source_events",
        "source_event_map",
        "source_event_proposals",
        "coverage",
        "series",
        "identity_links",
        "link_candidates",
        "identity_link_resolutions",
        "identity_link_history",
        "identity_reference_bindings",
        "identity_reference_migrations",
        "identity_decisions",
        "identity_journal_acceptances",
        "identity_source_refs",
        "findings",
        "observations",
        "snapshots",
        "history_acceptance",
    }
)

#: A read that feeds a side effect, not the unit's output (a confirmation
#: watch, a revision bump). No dependency needs to cover it.
EFFECT_ONLY = "effect_only"
#: The unit reads rows it owns: the old rows `replace_scope` replaces, or
#: output it wrote earlier in the same body. The generation covers them.
OWN_OUTPUT = "own_output"

#: A unit the body itself materializes (a source event mints its event): that
#: unit's own capture declares the read.
NESTED = "nested_unit"
#: A read no declared dependency covers yet; the named plan package adds it.
#: Counted as covered so the known gap is listed here, not in every summary.
PENDING = "pending:"


def _covers(*covers: str) -> str:
    return " | ".join(covers)


_OBSERVED = "source_observations"
_EVENT_OUTPUT = dict.fromkeys(
    (
        "events",
        "contests",
        "rounds",
        "entries",
        "heats",
        "judges",
        "callback_marks",
        "callbacks",
        "final_marks",
        "placements",
    ),
    OWN_OUTPUT,
)
# The event's own reads, and its effects on links it removes.
_NESTED_EVENT = dict.fromkeys(
    (*_EVENT_OUTPUT, "observations", "snapshots", "identity_links", "link_candidates"), NESTED
)

#: How each unit kind's declared dependencies (`derivation_dependencies`)
#: cover the canonical tables it reads: a dependency entry key
#: (`source_observations`, `selected_project_calendar`), a link read key
#: family (`project/dancer`, `content:journal`), `EFFECT_ONLY`,
#: `OWN_OUTPUT`, `NESTED` or `PENDING`. Several sites of one
#: table are joined by ` | `. Measured on the core suite, armed (WP2).
RULES: dict[tuple[str, str], Mapping[str, str]] = {
    ("project", "calendar"): {
        "observations": _OBSERVED,
        "snapshots": _OBSERVED,
        **_EVENT_OUTPUT,
        # reconcile_registry_events matches every event to registry
        # placements; the years own the association it writes (F23).
        "events": _covers(OWN_OUTPUT, EFFECT_ONLY),
        "registry_placements": EFFECT_ONLY,
        "findings": _covers(OWN_OUTPUT, EFFECT_ONLY),
    },
    ("project", "source_index"): {
        "observations": _OBSERVED,
        "snapshots": _OBSERVED,
        # A listing sits on several pages; the group holds every page's
        # observations, so another page's rows are covered too (F19).
        "source_events": _covers(OWN_OUTPUT, _OBSERVED),
        "findings": OWN_OUTPUT,
    },
    ("project", "dancer"): {
        "observations": _OBSERVED,
        "snapshots": _OBSERVED,
        # Its own row; other dancers' names are read by the pool probe, whose
        # answer bumps link pool versions, not the dancer's output (F20).
        "dancers": _covers(OWN_OUTPUT, EFFECT_ONLY),
        "registry_placements": _covers(OWN_OUTPUT, EFFECT_ONLY),
        # Reconcile for this dancer, and the link hints it enqueues.
        "events": EFFECT_ONLY,
        "findings": _covers(OWN_OUTPUT, EFFECT_ONLY),
        # The pool probe's subjects and candidates (`link/pool`).
        "entries": EFFECT_ONLY,
        "judges": EFFECT_ONLY,
        "link_candidates": EFFECT_ONLY,
    },
    ("project", "inventory"): {
        "observations": "calendar_observations",
        "snapshots": _covers("calendar_observations", "source_snapshot_times"),
        "source_events": "source_inventory",
        "registry_placements": "registry_inventory",
        "events": _covers("selected_project_calendar", OWN_OUTPUT),
        "series": OWN_OUTPUT,
        "findings": OWN_OUTPUT,
        # Foreign-key actions of its event upserts read the child tables.
        "contests": OWN_OUTPUT,
        "entries": OWN_OUTPUT,
        "judges": OWN_OUTPUT,
        "placements": OWN_OUTPUT,
    },
    ("project", "mapping"): {
        # The catalog it matches against comes from the calendars and the
        # inventory; its source's listings are a content group (F19). The
        # registry series names of the series rule are the inventory's (D-0253).
        "events": _covers("selected_project_calendar", "selected_project_inventory"),
        "series": "selected_project_inventory",
        "source_events": "source_listings",
        "source_event_proposals": OWN_OUTPUT,
    },
    ("project", "resolution"): {
        # The proposals naming its event are a content group. The row it
        # keeps is its own, a calendar's or the inventory's.
        "source_event_proposals": "resolution_proposals",
        "events": _covers("selected_project_calendar", "selected_project_inventory", OWN_OUTPUT),
        "source_event_map": OWN_OUTPUT,
        # Deleting its row detaches registry placements (D-0191), and the
        # foreign-key checks of that delete read the event's child tables.
        "registry_placements": EFFECT_ONLY,
        **{table: EFFECT_ONLY for table in _EVENT_OUTPUT if table != "events"},
        "findings": OWN_OUTPUT,
    },
    ("project", "event"): {
        "observations": _OBSERVED,
        "snapshots": _OBSERVED,
        "source_event_map": "source_mapping",
        **_EVENT_OUTPUT,
        "findings": OWN_OUTPUT,
        # Removing an entry, judge or event drops its links and detaches
        # registry placements (D-0191).
        "identity_links": EFFECT_ONLY,
        "link_candidates": EFFECT_ONLY,
        "registry_placements": EFFECT_ONLY,
    },
    ("project", "source_event"): {
        # A proxy: the event unit it runs owns the output.
        **_NESTED_EVENT,
        "observations": _covers(_OBSERVED, NESTED),
        "source_event_map": _covers("source_mapping", NESTED),
        "registry_placements": NESTED,
        "findings": NESTED,
    },
    ("project", "year"): {
        # Its events' rows come from the calendars, the inventory, the
        # resolutions and the event units; it writes their tiers.
        "events": _covers(
            "selected_project_calendar",
            "selected_project_inventory",
            "selected_project_resolution",
            "selected_project_event",
            OWN_OUTPUT,
        ),
        "contests": "selected_project_event",
        "entries": "selected_project_event",
        "rounds": "selected_project_event",
        # Its placements without the association it writes (F23); another
        # year's association of its events' placements is content too.
        "registry_placements": _covers("year_placements", OWN_OUTPUT),
        "coverage": OWN_OUTPUT,
        "history_acceptance": "year_acceptance",
        "findings": "year_findings",
        "snapshots": "snapshot_transport",
    },
    ("link", "event"): {
        # Read keys (plan F20, `derivation_dependencies.READ_FAMILIES`). The
        # event's rows are its event unit's generation; its events row is the
        # calendar's, the inventory's or a resolution's.
        "events": _covers("project/event", "content:event_row"),
        "contests": "project/event",
        "rounds": "project/event",
        "placements": "project/event",
        # Another event's subject bound to one of its references.
        "entries": _covers("project/event", "content:binding_ref"),
        "judges": _covers("project/event", "content:binding_ref"),
        "observations": _OBSERVED,
        "snapshots": _OBSERVED,
        "source_event_map": _OBSERVED,
        # The dancers it used, and its pool of every other dancer.
        "dancers": _covers("project/dancer", "pool"),
        # Confirmations and points; confirmation watches are effects.
        "registry_placements": _covers("content:registry_event", "project/dancer", EFFECT_ONLY),
        "findings": _covers("content:missing_identity", OWN_OUTPUT),
        "identity_decisions": "content:journal",
        "identity_reference_migrations": "content:journal",
        "identity_source_refs": _covers("content:journal", OWN_OUTPUT),
        "identity_reference_bindings": _covers(OWN_OUTPUT, "content:binding_ref"),
        "identity_links": OWN_OUTPUT,
        "link_candidates": OWN_OUTPUT,
        "identity_link_resolutions": _covers(OWN_OUTPUT, "content:binding_ref"),
        "identity_link_history": OWN_OUTPUT,
    },
}


def pending() -> tuple[str, ...]:
    """Every known gap RULES names, as `stage/kind:table: cover`."""
    return tuple(
        f"{stage}/{kind}:{table}: {cover}"
        for (stage, kind), tables in sorted(RULES.items())
        for table, covers in sorted(tables.items())
        for cover in covers.split(" | ")
        if cover.startswith(PENDING)
    )


class UnrecordedRead(RuntimeError):
    """Armed mode only: a canonical read the recorder could not account for."""


@dataclass
class Recorder:
    """What one unit body read: canonical tables and explicit read keys."""

    unit: WorkUnit
    tables: set[str] = field(default_factory=set)
    keys: set[str] = field(default_factory=set)
    #: Tables found only by the armed-mode query-plan check.
    blind: set[str] = field(default_factory=set)

    def record(self, key: str) -> None:
        self.keys.add(key)


@dataclass(eq=False)
class _Frame:
    conn: sqlite3.Connection
    recorder: Recorder | None
    authorizer: Authorizer | None
    statements: set[str] = field(default_factory=set)
    violations: list[str] = field(default_factory=list)


# Keyed by id(conn): sqlite3.Connection supports neither attributes nor weak
# references. Entries hold the connection, so an id is never reused while
# its entry exists. The pipeline writes on the main thread only.
_stack: dict[int, list[_Frame]] = {}
_installed: dict[int, tuple[sqlite3.Connection, Authorizer]] = {}
_undeclared: Counter[str] = Counter()
#: Safety signals of the read-set machinery, by name: `read_set_widened` (a
#: link read a table no rule maps) and `pool_probe_widened` (a dancer's pool
#: probe failed and bumped every event sharing an initial). Both should stay
#: 0; derive counts them per tick and the watchdog alarms on any.
_signals: Counter[str] = Counter()

_JOIN = re.compile(r"\b(?:USING|NATURAL)\b", re.IGNORECASE)
_OPEN_OPS = frozenset({"OpenRead", "ReopenIdx"})
_ENTRY_POINTS = frozenset(
    {
        ("swingset.project.process", "process_unit"),
        ("swingset.link.service", "link_event"),
    }
)
#: Derivation infrastructure: capture, currentness and completion read to
#: compute a unit's declared dependencies and output, not its input. A read
#: whose innermost pipeline frame is here is neither recorded nor guarded.
INFRASTRUCTURE = frozenset(
    {
        "swingset.project.materialization",
        "swingset.state.derivation_dependencies",
        "swingset.state.derivation_query",
        "swingset.state.derivation_readiness",
        "swingset.state.derivation_signatures",
        "swingset.state.derivations",
    }
)


def armed() -> bool:
    return os.environ.get("SWINGSET_READS_ARMED") == "1"


def set_authorizer(conn: sqlite3.Connection, authorizer: Authorizer | None) -> None:
    """Install an authorizer that a later `recording` chains to and restores.

    SQLite cannot report the current authorizer, so one set directly with
    `conn.set_authorizer` is replaced for a unit body and not restored.
    """
    if authorizer is None:
        _installed.pop(id(conn), None)
    else:
        _installed[id(conn)] = (conn, authorizer)
    _current(conn)


def active(conn: sqlite3.Connection) -> Recorder | None:
    """The recorder of the innermost open recording on `conn`, if any."""
    for frame in reversed(_stack.get(id(conn), ())):
        if frame.recorder is not None:
            return frame.recorder
    return None


def record(conn: sqlite3.Connection, key: str) -> None:
    """Add an explicit read key to the open recording on `conn`."""
    recorder = active(conn)
    if recorder is not None:
        recorder.record(key)
    elif armed():
        _report({"violation": "record_outside_recording", "key": key})
        raise UnrecordedRead(f"read key {key} recorded outside a unit recording")


def undeclared(
    unit: WorkUnit,
    tables: set[str],
    rules: Mapping[tuple[str, str], Mapping[str, str]] = RULES,
) -> tuple[str, ...]:
    """The read tables no rule of the unit's kind covers."""
    covered = rules.get((unit.stage, unit.unit_kind), {})
    return tuple(sorted(table for table in tables if table not in covered))


def drain() -> dict[str, int]:
    """Undeclared reads counted since the last drain, as `stage/kind:table`."""
    counts = dict(_undeclared)
    _undeclared.clear()
    return counts


def signal(name: str) -> None:
    """Count one read-set safety signal (`_signals`)."""
    _signals[name] += 1


def drain_signals() -> dict[str, int]:
    """Read-set safety signals counted since the last drain, by name."""
    counts = dict(_signals)
    _signals.clear()
    return counts


@contextmanager
def recording(
    conn: sqlite3.Connection,
    unit: WorkUnit,
    rules: Mapping[tuple[str, str], Mapping[str, str]] = RULES,
) -> Iterator[Recorder]:
    """Record the canonical tables `unit`'s body reads on `conn`.

    A nested recording on the same connection shares the outer recorder, and
    only the outermost checks the rules. The previous authorizer is chained
    while the body runs and restored on exit.
    """
    outer = active(conn)
    if outer is not None:
        yield outer
        return
    recorder = Recorder(unit)
    frame = _Frame(conn, recorder, _previous(conn))
    trace = armed()
    _push(frame)
    if trace:
        conn.set_trace_callback(_tracer(frame))
    try:
        yield recorder
    finally:
        if trace:
            conn.set_trace_callback(None)
        _pop(frame)
    if trace:
        _close_blind_spot(frame)
    _finish(recorder, rules)


def _tracer(frame: _Frame) -> Callable[[str], None]:
    def traced(statement: str) -> None:
        if _JOIN.search(statement) and not statement.startswith("--") and not _infrastructure():
            frame.statements.add(statement)

    return traced


@contextmanager
def guarding(conn: sqlite3.Connection) -> Iterator[None]:
    """Armed mode only: an entry point's window, outside its recordings.

    A canonical read by project or link body code in the window, outside a
    recording, is denied and raises `UnrecordedRead` when the window closes.
    Infrastructure reads (currentness, capture, completion) pass.
    """
    if not armed():
        yield
        return
    frame = _Frame(conn, None, _previous(conn))
    _push(frame)
    failure: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _pop(frame)
        if frame.violations:
            raise UnrecordedRead("; ".join(frame.violations)) from failure


def _previous(conn: sqlite3.Connection) -> Authorizer | None:
    """The tracked authorizer every frame chains to; frames never chain frames."""
    installed = _installed.get(id(conn))
    return installed[1] if installed is not None else None


def _push(frame: _Frame) -> None:
    _stack.setdefault(id(frame.conn), []).append(frame)
    frame.conn.set_authorizer(_callback(frame))


def _pop(frame: _Frame) -> None:
    frames = _stack[id(frame.conn)]
    frames.remove(frame)
    if not frames:
        del _stack[id(frame.conn)]
    _current(frame.conn)


def _current(conn: sqlite3.Connection) -> None:
    """Install whatever is now innermost: a frame, a tracked authorizer, none."""
    frames = _stack.get(id(conn))
    if frames:
        conn.set_authorizer(_callback(frames[-1]))
        return
    installed = _installed.get(id(conn))
    conn.set_authorizer(installed[1] if installed is not None else None)


def _callback(frame: _Frame) -> Authorizer:
    previous = frame.authorizer

    def authorize(
        action: int,
        table: str | None,
        column: str | None,
        database: str | None,
        source: str | None,
    ) -> int:
        # A read inside a trigger (source is its name) is the write's own
        # bookkeeping, not the body's input. No view reads a canonical table.
        if (
            action == sqlite3.SQLITE_READ
            and source is None
            and table in CANONICAL
            and database in (None, "main")
        ):
            recorder = frame.recorder
            if recorder is not None:
                if table not in recorder.tables and not _infrastructure():
                    recorder.tables.add(table)
            else:
                body = _body_frame()
                if body is not None:
                    message = f"{table} read by {body} outside a unit recording"
                    frame.violations.append(message)
                    _report({"violation": "outside_recording", "table": table, "at": body})
                    return sqlite3.SQLITE_DENY
        if previous is not None:
            return previous(action, table, column, database, source)
        return sqlite3.SQLITE_OK

    return authorize


def _infrastructure() -> bool:
    """Whether the innermost pipeline frame that issued the SQL is infrastructure.

    Called from a SQLite callback: frame 2 is the code that issued the SQL.
    """
    current: FrameType | None = sys._getframe(2)
    while current is not None:
        module = str(current.f_globals.get("__name__", ""))
        if module.startswith("swingset."):
            return module in INFRASTRUCTURE
        current = current.f_back
    return False


def _body_frame() -> str | None:
    """The project or link body function that issued the SQL, if any.

    Infrastructure issuing it is not the body, even on the body's behalf. The
    walk stops at the entry point: everything above it is its caller.
    """
    current: FrameType | None = sys._getframe(2)
    innermost = True
    while current is not None:
        module = str(current.f_globals.get("__name__", ""))
        name = current.f_code.co_name
        if module.startswith("swingset."):
            if innermost and module in INFRASTRUCTURE:
                return None
            innermost = False
        if (module, name) in _ENTRY_POINTS:
            return None
        if module.startswith(("swingset.project.", "swingset.link.")) and (
            module not in INFRASTRUCTURE
        ):
            return f"{module}.{name}"
        current = current.f_back
    return None


def _close_blind_spot(frame: _Frame) -> None:
    """Armed mode: add the tables a USING or NATURAL join's plan opens."""
    recorder = frame.recorder
    if recorder is None or not frame.statements:
        return
    conn = frame.conn
    roots = {
        int(page): str(table)
        for page, table in conn.execute(
            "SELECT rootpage,tbl_name FROM sqlite_schema WHERE rootpage>0"
        )
    }
    conn.set_authorizer(None)
    try:
        for statement in sorted(frame.statements):
            head = statement.lstrip().split(None, 1)[0].upper()
            if head not in {"SELECT", "WITH"}:
                continue  # A write's plan also opens foreign-key parents.
            try:
                plan = conn.execute("EXPLAIN " + statement).fetchall()
            except sqlite3.Error:
                continue  # A temporary table the body dropped.
            for row in plan:
                # addr, opcode, p1 (cursor), p2 (root page), p3 (database)
                if row[1] in _OPEN_OPS and row[4] == 0:
                    table = roots.get(int(row[3]))
                    if table in CANONICAL and table not in recorder.tables:
                        recorder.blind.add(table)
    finally:
        _current(conn)
    recorder.tables |= recorder.blind


def _finish(recorder: Recorder, rules: Mapping[tuple[str, str], Mapping[str, str]]) -> None:
    unit = recorder.unit
    missing = undeclared(unit, recorder.tables, rules)
    name = f"{unit.stage}/{unit.unit_kind}/{unit.unit_id}"
    _report(
        {
            "unit": name,
            "tables": sorted(recorder.tables),
            "keys": len(recorder.keys),
            "blind": sorted(recorder.blind),
            "undeclared": list(missing),
        }
    )
    if not missing:
        return
    for table in missing:
        _undeclared[f"{unit.stage}/{unit.unit_kind}:{table}"] += 1
    log("read_undeclared", unit=name, tables=list(missing))
    if armed():
        raise UnrecordedRead(f"{name} read undeclared tables: {', '.join(missing)}")


def _report(line: dict[str, object]) -> None:
    path = os.environ.get("SWINGSET_READS_REPORT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")
