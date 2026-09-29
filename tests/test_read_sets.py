"""The read recorder: what a unit body reads, and the wrapper that guarantees it."""

import ast
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from swingset.state import read_sets
from swingset.state.read_sets import (
    EFFECT_ONLY,
    OWN_OUTPUT,
    RULES,
    UnrecordedRead,
    guarding,
    record,
    recording,
)
from swingset.state.work import WorkUnit

EVENT = WorkUnit("project", "event", "e1")
LINK = WorkUnit("link", "event", "e1")
ROOT = Path(__file__).parents[1]
SRC = ROOT / "src/swingset"


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.executescript(
        "CREATE TABLE events(event_id TEXT PRIMARY KEY, year INTEGER);"
        "CREATE TABLE contests(contest_id TEXT PRIMARY KEY, event_id TEXT);"
        "CREATE TABLE findings(finding_id TEXT PRIMARY KEY, event_id TEXT);"
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE audit(n INTEGER);"
        "CREATE TRIGGER contests_audit AFTER INSERT ON contests "
        "BEGIN INSERT INTO audit SELECT count(*) FROM findings; END;"
        "INSERT INTO events VALUES ('e1',2026);"
        "INSERT INTO contests VALUES ('c1','e1');"
    )
    yield connection
    connection.close()


@pytest.fixture
def production(monkeypatch):
    monkeypatch.delenv("SWINGSET_READS_ARMED", raising=False)
    read_sets.drain()


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv("SWINGSET_READS_ARMED", "1")
    read_sets.drain()


def rules(**tables):
    return {("project", "event"): tables}


def test_the_authorizer_records_every_table_of_a_join(conn, production):
    with recording(conn, EVENT, rules(events="e", contests="c")) as recorder:
        conn.execute(
            "SELECT c.contest_id FROM contests c JOIN events e ON e.event_id=c.event_id"
        ).fetchall()
        conn.execute("SELECT 1 FROM meta").fetchall()
    assert recorder.tables == {"events", "contests"}
    assert read_sets.drain() == {}


def test_a_table_read_only_as_a_filter_is_recorded(conn, production):
    with recording(conn, EVENT, rules(events="e", contests="c")) as recorder:
        conn.execute("SELECT count(*) FROM events, contests").fetchall()
    assert recorder.tables == {"events", "contests"}


def test_armed_mode_finds_a_using_join_the_authorizer_misses(conn, monkeypatch):
    statement = "SELECT count(*) FROM contests JOIN events USING(event_id)"
    monkeypatch.delenv("SWINGSET_READS_ARMED", raising=False)
    with recording(conn, EVENT, rules(events="e", contests="c")) as recorder:
        conn.execute(statement).fetchall()
    # SQLite's documented gap: no column of either table is named.
    assert recorder.tables == set()
    monkeypatch.setenv("SWINGSET_READS_ARMED", "1")
    with recording(conn, EVENT, rules(events="e", contests="c")) as recorder:
        conn.execute(statement).fetchall()
    assert recorder.tables == recorder.blind == {"events", "contests"}


def test_reads_inside_a_trigger_are_the_writes_bookkeeping(conn, production):
    with recording(conn, EVENT, rules(contests=OWN_OUTPUT)) as recorder:
        conn.execute("INSERT INTO contests VALUES ('c2','e1')")
    assert "findings" not in recorder.tables


def test_a_nested_recording_shares_the_outer_recorder(conn, production):
    with recording(conn, EVENT, rules(events="e", contests="c")) as outer:
        with recording(conn, WorkUnit("project", "calendar", "2026")) as inner:
            conn.execute("SELECT contest_id FROM contests").fetchall()
        assert inner is outer
        conn.execute("SELECT year FROM events").fetchall()
        record(conn, "project/event/e1")
    assert outer.tables == {"events", "contests"}
    assert outer.keys == {"project/event/e1"}
    # The inner exit restored nothing: reads after it were still recorded.
    assert read_sets.active(conn) is None


def test_the_previous_authorizer_is_chained_and_restored(conn, production):
    seen = []

    def deny_meta(action, table, column, database, source):
        seen.append(table)
        return sqlite3.SQLITE_DENY if table == "meta" else sqlite3.SQLITE_OK

    read_sets.set_authorizer(conn, deny_meta)
    try:
        with recording(conn, EVENT, rules(events="e")):
            conn.execute("SELECT year FROM events").fetchall()
            with pytest.raises(sqlite3.DatabaseError, match="prohibited|not authorized"):
                conn.execute("SELECT value FROM meta").fetchall()
        seen.clear()
        with pytest.raises(sqlite3.DatabaseError, match="prohibited|not authorized"):
            conn.execute("SELECT value FROM meta WHERE key='restored'").fetchall()
        assert "meta" in seen
    finally:
        read_sets.set_authorizer(conn, None)
    conn.execute("SELECT value FROM meta WHERE key='cleared'").fetchall()


def test_an_unmapped_table_is_counted_and_logged_but_never_fails(conn, production, capsys):
    with recording(conn, EVENT, rules(events="e")) as recorder:
        conn.execute("SELECT year FROM events").fetchall()
        conn.execute("SELECT finding_id FROM findings").fetchall()
    assert recorder.tables == {"events", "findings"}
    assert read_sets.drain() == {"project/event:findings": 1}
    assert read_sets.drain() == {}
    assert "read_undeclared" in capsys.readouterr().err


def test_effect_only_reads_are_not_reported(conn, production, capsys):
    with recording(conn, EVENT, rules(events="e", findings=EFFECT_ONLY)):
        conn.execute("SELECT finding_id FROM findings").fetchall()
    assert read_sets.drain() == {}
    assert "read_undeclared" not in capsys.readouterr().err


def test_armed_mode_raises_on_an_undeclared_table_at_unit_end(conn, armed):
    with pytest.raises(UnrecordedRead, match="findings"):
        with recording(conn, EVENT, rules(events="e")):
            conn.execute("SELECT finding_id FROM findings").fetchall()


def test_armed_mode_raises_on_a_body_read_outside_recording(conn, armed):
    # A module-level function whose globals name a project body module.
    namespace = {"__name__": "swingset.project.example"}
    exec(
        "def body(conn):\n    return conn.execute('SELECT year FROM events').fetchall()", namespace
    )
    infrastructure = {"__name__": "swingset.state.example"}
    # Another text: SQLite authorizes at prepare, and a cached statement is
    # not prepared again until the authorizer changes.
    exec(
        "def check(conn):\n    return conn.execute('SELECT event_id FROM events').fetchall()",
        infrastructure,
    )
    with pytest.raises(UnrecordedRead, match="swingset.project.example.body"):
        with guarding(conn):
            infrastructure["check"](conn)
            try:
                namespace["body"](conn)
            except sqlite3.DatabaseError:
                pass  # A body that swallows the denial still fails the window.
    with guarding(conn):
        with recording(conn, EVENT, rules(events="e")):
            namespace["body"](conn)
    with pytest.raises(UnrecordedRead):
        record(conn, "project/event/e1")


def test_production_mode_never_guards(conn, production):
    namespace = {"__name__": "swingset.project.example"}
    exec(
        "def body(conn):\n    return conn.execute('SELECT year FROM events').fetchall()", namespace
    )
    with guarding(conn):
        assert namespace["body"](conn) == [(2026,)]
    record(conn, "ignored")


def _dependency_keys():
    """Entry keys `derivation_dependencies.dependencies` can write."""
    tree = ast.parse((SRC / "state/derivation_dependencies.py").read_text())
    groups = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "group"
        and isinstance(node.args[0], ast.Constant)
    }
    from swingset.state.derivation_dependencies import PROJECT, READ_FAMILIES

    return groups | {"selected_project_" + kind for kind in PROJECT} | READ_FAMILIES


def test_every_rule_names_a_canonical_table_and_a_known_cover():
    known = _dependency_keys() | {EFFECT_ONLY, OWN_OUTPUT, read_sets.NESTED}
    for kind, tables in RULES.items():
        assert set(tables) <= read_sets.CANONICAL, kind
        for table, covers in tables.items():
            for cover in covers.split(" | "):
                assert cover in known or cover.startswith(read_sets.PENDING), (kind, table, cover)
    # A known gap is listed here, so a new one needs a reviewed edit.
    assert read_sets.pending() == ()


# The pipeline fixtures, run armed: every project kind and the link read only
# what their rules cover, and no body code reads outside a recording.
ARMED_FIXTURES = (
    "tests/test_h15_acceptance.py",
    "tests/test_projection_materialization.py",
    "tests/test_link_service.py",
    "tests/test_project.py",
    # Link read sets and the pool probe of dancer units (plan F20).
    "tests/test_link_read_sets.py",
)


def test_pipeline_fixtures_run_armed_with_zero_undeclared_reads(tmp_path):
    report = tmp_path / "reads.jsonl"
    environment = {
        **os.environ,
        "SWINGSET_READS_ARMED": "1",
        "SWINGSET_READS_REPORT": str(report),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / "run"),
            *ARMED_FIXTURES,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    lines = [json.loads(line) for line in report.read_text().splitlines()]
    violations = [line for line in lines if "violation" in line or line.get("undeclared")]
    assert violations == []
    kinds = {tuple(line["unit"].split("/")[:2]) for line in lines}
    assert set(RULES) <= kinds, sorted(set(RULES) - kinds)


# Static wrapper check (plan section 10): project/ and link/ reach the state
# only through the entry points that open a recording.
PACKAGES = ("swingset.project", "swingset.link")
ENTRY = {
    "swingset.project.process.process_unit",
    "swingset.link.service.link_event",
    "swingset.project.history.accept_year",
}
ROUTE = "swingset.schedule.derive.derive_one"
FORBIDDEN_CALLS = {"connect", "set_authorizer", "set_trace_callback"}
CONNECTING = {"connection", "transaction", "reader"}
CONNECTION_PARAMETERS = {"conn", "connection", "db", "database"}

#: Functions in project/ or link/ that read and are reached some other way.
ALLOWED = {
    "swingset.project.history.phase_two_allowed": (
        "acquisition gate query: its answer steers fetching, never a derived output"
    ),
    "swingset.project.history.pending_inventory_projection": (
        "backfill gate query: reports whether the inventory projection waits"
    ),
    "swingset.project.history.projection_pending": (
        "loop plan gate query: reports whether project work waits"
    ),
    "swingset.project.history.year_gate_reason": (
        "loop plan gate query: reports why a year is gated"
    ),
    "swingset.project.history.reconcile_history": (
        "a database without derivations: the review pack's fallback reconciles "
        "the inventory and history whole, with no unit to record (history/review.py)"
    ),
    "swingset.project.materialization.output_rows": (
        "verify sample (state/verify.py): labels a unit's live output around the unit's "
        "own write, which it rolls back"
    ),
    "swingset.project.materialization.helper_recipe": (
        "verify sample: captures a link's selection as derive does, so a supersession raises"
    ),
    "swingset.link.decisions.DecisionResolver": (
        "release build identity policy: the build's reads are the release's, not a unit's"
    ),
    "swingset.link.evaluation.main": (
        "report-only research tool (`python -m swingset.link.evaluation`) on a read-only copy"
    ),
    "swingset.link.evaluation_packet.main": (
        "report-only research tool (`python -m swingset.link.evaluation_packet`)"
    ),
}


def _module_name(path):
    parts = path.relative_to(SRC.parent).with_suffix("").parts
    name = ".".join(parts)
    return name.removesuffix(".__init__")


def _modules():
    return {
        _module_name(path): (path, ast.parse(path.read_text()))
        for path in sorted(SRC.rglob("*.py"))
    }


def _in_packages(module):
    return module.startswith(tuple(package + "." for package in PACKAGES)) or module in PACKAGES


def _absolute(module, node):
    if node.level == 0:
        return node.module
    base = module.split(".")
    package = base if module in PACKAGES or _is_package(module) else base[:-1]
    package = package[: len(package) - node.level + 1]
    return ".".join(package + ([node.module] if node.module else []))


def _is_package(module):
    return (SRC.parent / module.replace(".", "/") / "__init__.py").exists()


def _reads(node):
    if isinstance(node, ast.ClassDef):
        return any(_reads(item) for item in node.body if isinstance(item, ast.FunctionDef))
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return False
    arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
    return any(
        argument.arg in CONNECTION_PARAMETERS
        or (argument.annotation is not None and "Connection" in ast.unparse(argument.annotation))
        or (argument.annotation is not None and "Database" in ast.unparse(argument.annotation))
        for argument in arguments
    )


class _Graph:
    """Top-level definitions of project/ and link/, and who references them."""

    def __init__(self):
        self.modules = _modules()
        self.definitions = {}
        for module, (_, tree) in self.modules.items():
            if _in_packages(module):
                for node in tree.body:
                    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                        self.definitions[f"{module}.{node.name}"] = node
        self.imports = {
            module: self._imports(module, tree) for module, (_, tree) in self.modules.items()
        }

    def _imports(self, module, tree):
        names = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                source = _absolute(module, node)
                for alias in node.names:
                    names[alias.asname or alias.name] = self.resolve(f"{source}.{alias.name}")
        if _in_packages(module):
            for node in tree.body:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    names.setdefault(node.name, f"{module}.{node.name}")
        return names

    def resolve(self, qualified, depth=0):
        """Follow a package `__init__` re-export to its definition."""
        module, _, name = qualified.rpartition(".")
        if depth > 5 or module not in self.modules or not _is_package(module):
            return qualified
        _, tree = self.modules[module]
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) == name:
                        return self.resolve(f"{_absolute(module, node)}.{alias.name}", depth + 1)
        return qualified

    def references(self):
        """(referrer, target): a top-level function or module naming a definition."""
        for module, (_, tree) in self.modules.items():
            names = self.imports[module]
            for node in tree.body:
                if isinstance(node, ast.Import | ast.ImportFrom):
                    continue  # Importing is not using.
                referrer = f"{module}.{getattr(node, 'name', '<module>')}"
                for inner in ast.walk(node):
                    target = None
                    if isinstance(inner, ast.Name) and inner.id in names:
                        target = names[inner.id]
                    elif (
                        isinstance(inner, ast.Attribute)
                        and isinstance(inner.value, ast.Name)
                        and names.get(inner.value.id) in self.modules
                    ):
                        target = self.resolve(f"{names[inner.value.id]}.{inner.attr}")
                    if target in self.definitions and target != referrer:
                        yield referrer, target


def _outside(referrer):
    return not _in_packages(referrer.rpartition(".")[0])


def _opens_connection(node):
    """Reaches a live connection: `database.connection`, `.transaction()`, `.reader()`."""
    return any(
        isinstance(inner, ast.Attribute)
        and (
            inner.attr == "connection"
            or (inner.attr in {"transaction", "reader"} and ast.unparse(inner.value) != "csv")
        )
        for inner in ast.walk(node)
    )


def test_project_and_link_never_open_connections_or_authorizers():
    graph = _Graph()
    found = []
    for module, (path, tree) in graph.modules.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and not _in_packages(module):
                # Plain imports would hide references from the graph below.
                found.extend(
                    f"{module}: import {alias.name}"
                    for alias in node.names
                    if _in_packages(alias.name)
                )
            if not _in_packages(module) or not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Attribute) and (
                function.attr in {"set_authorizer", "set_trace_callback"}
                or (function.attr == "connect" and ast.unparse(function.value) == "sqlite3")
            ):
                found.append(f"{path.name}:{node.lineno} {ast.unparse(function)}")
    assert found == []


def test_project_and_link_are_reached_only_through_their_entry_points():
    graph = _Graph()
    references = set(graph.references())
    used = set()
    external = []
    for referrer, target in sorted(references):
        if not _outside(referrer):
            continue
        if not _reads(graph.definitions[target]) or target in ENTRY or referrer == ROUTE:
            continue
        if target in ALLOWED:
            used.add(target)
        else:
            external.append(f"{referrer} -> {target}")
    assert external == []
    # Command-line tools inside the packages are entry points of their own.
    mains = sorted(name for name in graph.definitions if name.endswith(".main"))
    assert [name for name in mains if name not in ALLOWED] == []
    used.update(mains)
    # A live connection is reached only by an entry point, or by a function
    # that only code under a recording entry point calls.
    callers = {}
    for referrer, target in references:
        callers.setdefault(target, set()).add(referrer)
    reached = {"swingset.project.process.process_unit", "swingset.link.service.link_event"}
    changed = True
    while changed:
        changed = False
        for target, referrers in callers.items():
            if target not in reached and referrers and referrers <= reached:
                reached.add(target)
                changed = True
    opening = []
    for name, node in sorted(graph.definitions.items()):
        if name in ENTRY or not _opens_connection(node):
            continue
        if name in ALLOWED:
            used.add(name)
        elif name not in reached:
            opening.append(name)
    assert opening == []
    assert sorted(set(ALLOWED) - used) == []


def test_the_entry_points_open_a_recording():
    graph = _Graph()
    for name in ("swingset.project.process.process_unit", "swingset.link.service.link_event"):
        calls = {
            ast.unparse(inner.func)
            for inner in ast.walk(graph.definitions[name])
            if isinstance(inner, ast.Call)
        }
        assert {"recording", "guarding"} <= calls, name
