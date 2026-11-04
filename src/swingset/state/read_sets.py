"""Declared runtime read sets with PostgreSQL planner checks in armed tests.

Production uses RULES as its read set. Armed mode asks PostgreSQL's planner
for every unit-body statement's relations, resolving views and CTEs without
executing a second write. Infrastructure reads remain outside the unit body.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import FrameType

from swingset.log import log
from swingset.model.schema import TABLES

from .engine import Connection
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
    "undeclared",
]

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
    conn: Connection
    recorder: Recorder | None
    violations: list[str] = field(default_factory=list)


_stack: dict[int, list[_Frame]] = {}
_undeclared: Counter[str] = Counter()
_signals: Counter[str] = Counter()
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


def active(conn: Connection) -> Recorder | None:
    """The recorder of the innermost open recording on `conn`, if any."""
    for frame in reversed(_stack.get(id(conn), ())):
        if frame.recorder is not None:
            return frame.recorder
    return None


def record(conn: Connection, key: str) -> None:
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
    conn: Connection,
    unit: WorkUnit,
    rules: Mapping[tuple[str, str], Mapping[str, str]] = RULES,
) -> Iterator[Recorder]:
    outer = active(conn)
    if outer is not None:
        yield outer
        return
    recorder = Recorder(unit)
    frame = _Frame(conn, recorder)
    _stack.setdefault(id(conn), []).append(frame)
    try:
        if armed():
            with conn.observing(_observer(frame)):
                yield recorder
        else:
            recorder.tables.update(rules.get((unit.stage, unit.unit_kind), {}))
            yield recorder
    finally:
        _pop(frame)
    _finish(recorder, rules)


@contextmanager
def guarding(conn: Connection) -> Iterator[None]:
    if not armed():
        yield
        return
    frame = _Frame(conn, None)
    _stack.setdefault(id(conn), []).append(frame)
    failure: BaseException | None = None
    try:
        with conn.observing(_observer(frame)):
            yield
    except BaseException as error:
        failure = error
        raise
    finally:
        _pop(frame)
        if frame.violations:
            raise UnrecordedRead("; ".join(frame.violations)) from failure


def _pop(frame: _Frame) -> None:
    frames = _stack[id(frame.conn)]
    frames.remove(frame)
    if not frames:
        del _stack[id(frame.conn)]


def _observer(frame: _Frame) -> Callable[[object, object], None]:
    def observe(query: object, params: object) -> None:
        if _infrastructure():
            return
        if frame.recorder is None and active(frame.conn) is not None:
            return
        text = query.as_string(frame.conn) if hasattr(query, "as_string") else query
        if isinstance(text, bytes):
            text = text.decode(frame.conn.info.encoding)
        if not isinstance(text, str):
            raise TypeError("database statement is not SQL text")
        # Only statements that can read pipeline facts need planner checking.
        # The statement itself is sent unchanged; psycopg numbers its bound
        # parameters for EXPLAIN GENERIC_PLAN without embedding private values.
        head = _statement_head(text)
        if not head or head in {
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
            "SAVEPOINT",
            "RELEASE",
            "SET",
            "RESET",
            "SHOW",
        }:
            return
        if head not in {"SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "MERGE"}:
            raise UnrecordedRead(f"unit-body statement cannot be checked by the planner: {head}")
        tables = frame.conn.planned_relations(query, params) & CANONICAL
        if frame.recorder is not None:
            frame.recorder.tables.update(tables)
            frame.recorder.blind.update(tables)
            return
        body = _body_frame()
        if body is None:
            return
        for table in sorted(tables):
            message = f"{table} read by {body} outside a unit recording"
            frame.violations.append(message)
            _report({"violation": "outside_recording", "table": table, "at": body})
        if tables:
            raise UnrecordedRead("; ".join(frame.violations))

    return observe


def _statement_head(text: str) -> str:
    """Skip PostgreSQL line and nested block comments without rewriting SQL."""
    rest = text.lstrip()
    while rest.startswith(("--", "/*")):
        if rest.startswith("--"):
            rest = rest.partition("\n")[2].lstrip()
            continue
        depth, index = 1, 2
        while depth and index < len(rest):
            if rest.startswith("/*", index):
                depth, index = depth + 1, index + 2
            elif rest.startswith("*/", index):
                depth, index = depth - 1, index + 2
            else:
                index += 1
        if depth:
            raise UnrecordedRead("unterminated SQL comment in a unit statement")
        rest = rest[index:].lstrip()
    return rest.split(None, 1)[0].upper() if rest else ""


def _pipeline_frames() -> Iterator[FrameType]:
    current: FrameType | None = sys._getframe(2)
    while current is not None:
        module = str(current.f_globals.get("__name__", ""))
        if (
            module.startswith("swingset.")
            and module != __name__
            and not module.startswith("swingset.state.engine")
        ):
            yield current
        current = current.f_back


def _infrastructure() -> bool:
    return next(
        (
            str(frame.f_globals.get("__name__", "")) in INFRASTRUCTURE
            for frame in _pipeline_frames()
        ),
        False,
    )


def _body_frame() -> str | None:
    first = True
    for frame in _pipeline_frames():
        module = str(frame.f_globals.get("__name__", ""))
        name = frame.f_code.co_name
        if first and module in INFRASTRUCTURE:
            return None
        first = False
        if (module, name) in _ENTRY_POINTS:
            return None
        if (
            module.startswith(("swingset.project.", "swingset.link."))
            and module not in INFRASTRUCTURE
        ):
            return f"{module}.{name}"
    return None


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
