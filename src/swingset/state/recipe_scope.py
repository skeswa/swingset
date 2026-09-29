"""Which captured runtime files each code recipe names (D-0207).

A scope is the static import closure of a few entry modules, computed from
the captured bytes, never from the live package. The walk counts every
`import` and `from` statement anywhere in a module, including those inside
functions and `TYPE_CHECKING` blocks, and adds each module's parent packages,
since their `__init__` runs first. It also adds every non-Python file in the
directory of a module it reaches and in that directory's non-package
subdirectories (so `state/migrations/*.sql`). Fixtures are never part of a
recipe.

A shallow entry adds its own bytes and its parent packages' own bytes, but no
data files and none of their imports, since the storage imports reach the
package-wide import cycle. `schedule.parse` dispatches parse work; the
`PARSE_WRITERS` write what a parse unit leaves behind (admission decisions,
observations, verification, findings, watch clocks), so each is in every parse
scope. Tests pin what `schedule.parse` imports and which imports of a shallow
module fall outside its scope.

`sources.registry` resolves adapters with `importlib`; the extract and parse
entries name the owning adapter explicitly for that reason.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from swingset.fetch.archive import canonical, digest

PREFIX = "runtime/swingset/"
FORMAT = "swingset-scoped-recipe-v1"
#: The environment's identity: the interpreter and the installed distributions
#: (`--no-dev` on the worker), plus pyproject's `[project]` table. The whole
#: `pyproject.toml` and `uv.lock` stay in the bundle but not here: a tooling
#: edit (`[tool.*]`, a dev-dependency bump) would otherwise re-parse every
#: snapshot. A runtime dependency change moves the installed distributions.
ENV_FILES = ("runtime/environment.json", "runtime/project.json")
SCOPE_PREFIX = "recipe/code/"


@dataclass(frozen=True)
class Entry:
    """A module named without the `swingset.` prefix, for example `schedule.parse`."""

    module: str
    deep: bool = True


# The projector module of each project unit kind. `project.process` imports all
# of them, so today the closures agree. Phase 2 took the scheduler out of the
# import cycle, not the projectors; the table keeps the scope names stable for
# a later split.
PROJECTORS: dict[str, str] = {
    "calendar": "project.events",
    "dancer": "project.registry",
    "event": "project.contests",
    "inventory": "project.history",
    "mapping": "project.map",
    "resolution": "project.map",
    "source_event": "project.process",
    "source_index": "project.events",
    "year": "project.history",
}

# Modules that run inside a parse unit and write what it leaves behind:
# admission decisions and observation rewrites, verification, findings and
# watch clocks. Their own bytes enter every parse scope; their storage imports
# do not (see the module docstring).
PARSE_WRITERS: tuple[str, ...] = (
    "admission.select",
    "admission.support",
    "fetch.wayback",
    "history.acquisition",
    "history.intent",
    "history.origin_dispatch",
    "state.findings",
    "state.observations",
    "state.verification",
)

_ACQUIRE = "acquisition; it fetches, gates and stores pages that parse reads"
_EVENTS = "event acquisition bookkeeping; the cycle and plan loop run it"
_SCHEDULE = "scheduling; it runs stages through the modules the scopes name"
# Package paths no recipe names. A path ending in "/" covers its directory.
# Since `versions.py` left `schedule/cycle.py`, no project, link or build
# closure reaches the scheduler or acquisition (architecture plan, F4).
EXCLUDED: dict[str, str] = {
    "admission/corpus.py": "offline admission corpus export",
    "admission/enumeration_evidence.py": _EVENTS,
    "admission/event_declarations.py": _EVENTS,
    "admission/event_retirement.py": _EVENTS,
    "admission/parent_evidence.py": _EVENTS,
    "admission/policy.py": "operator commands that change admission policy rows",
    "backup/": "backups copy state and do not derive it",
    "build/event_page_evidence.py": "offline event-page evidence report",
    "build/identity_restriction.py": "offline identity restriction report",
    "cli.py": "command-line entry; it runs stages through the modules the scopes name",
    "fetch/cdx.py": _ACQUIRE,
    "fetch/client.py": _ACQUIRE,
    "fetch/controls.py": _ACQUIRE,
    "fetch/eligibility.py": _ACQUIRE,
    "fetch/limits.py": _ACQUIRE,
    "fetch/politeness.py": _ACQUIRE,
    "fetch/recovery.py": "restores retained artifacts from local checkpoints before a stage reads them",
    "fetch/robots.py": _ACQUIRE,
    "history/aliases.py": "operator alias review",
    "history/archive_timing.py": _ACQUIRE,
    "history/backfill.py": _ACQUIRE,
    "history/captures.py": _ACQUIRE,
    "history/catalog.py": "offline history catalog report",
    "history/closure.py": "year-gap review; it writes findings, not derivations",
    "history/discovery.py": "acquisition discovery commands",
    "history/event_site_intake.py": "acquisition intake commands",
    "history/event_site_queries.py": "acquisition intake queries",
    "history/event_sites.py": _ACQUIRE,
    "history/intake.py": "acquisition intake commands",
    "history/origin.py": _ACQUIRE,
    "history/platform.py": _ACQUIRE,
    "history/review.py": "operator year-gap review",
    "history/transfer.py": "operator transfer commands",
    "link/evaluation.py": "offline linker evaluation",
    "link/evaluation_input.py": "offline linker evaluation",
    "link/evaluation_packet.py": "offline linker evaluation",
    "link/overrides.py": "operator identity override commands",
    "loops/": "loop ticks; they run stages through the modules the scopes name",
    "schedule/acquisition.py": _ACQUIRE,
    "schedule/cycle.py": _SCHEDULE,
    "schedule/derive.py": _SCHEDULE,
    "schedule/discover.py": _ACQUIRE,
    "schedule/event_accounting.py": _EVENTS,
    "schedule/event_accounting_report.py": "event acquisition report",
    "schedule/event_blocker_history.py": _EVENTS,
    "schedule/event_capacity.py": _EVENTS,
    "schedule/event_enumerations.py": _EVENTS,
    "schedule/event_gaps.py": _EVENTS,
    "schedule/event_history.py": "event acquisition report",
    "schedule/event_inventory.py": "event acquisition report",
    "schedule/event_pressure.py": _EVENTS,
    "schedule/event_pressure_probe.py": _EVENTS,
    "schedule/event_pressure_refresh.py": _EVENTS,
    "schedule/event_progress.py": _EVENTS,
    "schedule/event_progress_report.py": "event acquisition report",
    "schedule/event_publication.py": "event acquisition report",
    "schedule/event_readiness.py": _EVENTS,
    "schedule/event_report.py": "event acquisition report",
    "schedule/event_request_kind.py": _EVENTS,
    "schedule/event_retirement.py": _EVENTS,
    "schedule/event_service.py": "event acquisition commands",
    "schedule/event_timing.py": _EVENTS,
    "schedule/event_timing_observer.py": _EVENTS,
    "schedule/event_turns.py": _EVENTS,
    "schedule/fairness.py": _SCHEDULE,
    "schedule/offline.py": _SCHEDULE,
    "schedule/registry.py": _ACQUIRE,
    "sources/dcn/": "unregistered source; no page kind resolves to it",
    "state/acquisition_plan.py": "loop plan rows; acquisition reads them, no derivation does",
    "state/closure_collection.py": "operator retention commands",
    "state/control_scopes.py": "control dependencies of admission and status; the stages check controls, not outputs",
    "state/control_time.py": "operator control commands",
    "state/event_progress.py": _EVENTS,
    "state/guards.py": "fetch guard reasons; acquisition writes them, no derivation reads them",
    "state/lag_report.py": "status report",
    "state/loop_state.py": "loop tick status; reports read it, no derivation does",
    "state/derivation_readiness.py": (
        "bulk dancer currentness; no derivation calls it since link read sets (plan F20)"
    ),
    "state/publication_report.py": "status report",
    "state/requirement_report.py": "status report",
    "state/retention_apply.py": "operator retention commands",
    "state/verify.py": "weekly verify sample; it re-runs units and rolls every write back",
}


def _skipped(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return "fixtures" in parts or "__pycache__" in parts or path.endswith(".pyc")


def package_paths(files: Mapping[str, bytes]) -> set[str]:
    """Captured package files a recipe could name, as `runtime/swingset/...` keys."""
    return {name for name in files if name.startswith(PREFIX) and not _skipped(name)}


def _module_name(path: str) -> str:
    parts = ["swingset", *PurePosixPath(path[len(PREFIX) :]).with_suffix("").parts]
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _parents(module: str) -> Iterable[str]:
    parts = module.split(".")
    return (".".join(parts[:index]) for index in range(1, len(parts)))


_NAMES: dict[tuple[str, str], tuple[str, ...]] = {}


def imported_names(path: str, body: bytes) -> tuple[str, ...]:
    """Absolute `swingset` names a module imports, and each `from` name under them.

    `from a import b` yields `a` and `a.b`, since `b` may be a submodule.
    Memoized by path and bytes, since a scoped capture parses every module.
    """
    key = (path, digest(body))
    known = _NAMES.get(key)
    if known is not None:
        return known
    module = _module_name(path)
    package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
    names: list[str] = []
    for node in ast.walk(ast.parse(body, filename=path)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                if node.level - 1 >= len(parts):
                    continue
                parts = parts[: len(parts) - node.level + 1]
                base = ".".join([*parts, *([node.module] if node.module else [])])
            else:
                base = node.module or ""
            names.append(base)
            names.extend(f"{base}.{alias.name}" for alias in node.names)
    result = tuple(
        sorted({name for name in names if name == "swingset" or name.startswith("swingset.")})
    )
    if len(_NAMES) >= 4096:
        _NAMES.clear()
    _NAMES[key] = result
    return result


class _Graph:
    def __init__(self, files: Mapping[str, bytes]) -> None:
        self.files = files
        paths = package_paths(files)
        self.modules = {_module_name(path): path for path in paths if path.endswith(".py")}
        self.data = sorted(path for path in paths if not path.endswith(".py"))
        self.packages = {
            str(PurePosixPath(path).parent) for path in paths if path.endswith("/__init__.py")
        }
        self._imports: dict[str, frozenset[str]] = {}
        # Each data file belongs to its own directory and, while no package
        # lies between, to every directory above it.
        self._data: dict[str, list[str]] = {}
        for name in self.data:
            directory = name.rpartition("/")[0]
            while True:
                self._data.setdefault(directory, []).append(name)
                if directory in self.packages or "/" not in directory:
                    break
                directory = directory.rpartition("/")[0]

    def imports(self, module: str) -> frozenset[str]:
        """Package modules one module imports anywhere in its body."""
        known = self._imports.get(module)
        if known is not None:
            return known
        path = self.modules[module]
        found = set()
        for name in imported_names(path, self.files[path]):
            for candidate in (*_parents(name), name):
                if candidate in self.modules:
                    found.add(candidate)
        result = frozenset(found - {module})
        self._imports[module] = result
        return result

    def data_files(self, path: str) -> Iterable[str]:
        """Non-Python files beside a module and in its non-package subdirectories."""
        return self._data.get(path.rpartition("/")[0], ())

    def closure(self, entries: Iterable[Entry]) -> set[str]:
        seen: set[str] = set()

        def walk(stack: list[str]) -> None:
            while stack:
                module = stack.pop()
                if module in seen:
                    continue
                seen.add(module)
                stack.extend(parent for parent in _parents(module) if parent in self.modules)
                stack.extend(self.imports(module))

        named = [entry for entry in entries if f"swingset.{entry.module}" in self.modules]
        walk([f"swingset.{entry.module}" for entry in named if entry.deep])
        paths = {self.modules[module] for module in seen}
        for path in list(paths):
            paths.update(self.data_files(path))
        # A shallow entry adds its own bytes and its parent packages' bytes:
        # none of them walked, and no data files beside them (the migrations
        # beside `state/observations.py` are the database's, not the parse's).
        for entry in named:
            name = f"swingset.{entry.module}"
            if not entry.deep:
                paths.update(
                    self.modules[module]
                    for module in (*_parents(name), name)
                    if module in self.modules
                )
        return paths


def closure(files: Mapping[str, bytes], entries: Iterable[Entry]) -> set[str]:
    """Captured paths that the entry modules can execute or read beside them."""
    return _Graph(files).closure(entries)


def module_imports(files: Mapping[str, bytes], module: str) -> frozenset[str]:
    """Package modules, including parent packages, that one module imports."""
    return _Graph(files).imports(module)


def _registry(files: Mapping[str, bytes]) -> tuple[dict[str, str], dict[str, str]]:
    """Read the adapter tables from the captured registry's literals."""
    body = files.get(PREFIX + "sources/registry.py")
    tables: dict[str, dict[str, str]] = {"ADAPTERS": {}, "KIND_OWNERS": {}}
    if body is None:
        return tables["ADAPTERS"], tables["KIND_OWNERS"]
    for node in ast.parse(body).body:
        target = (
            node.target
            if isinstance(node, ast.AnnAssign)
            else node.targets[0]
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            else None
        )
        value = getattr(node, "value", None)
        if isinstance(target, ast.Name) and target.id in tables and value is not None:
            tables[target.id] = dict(ast.literal_eval(value))
    return tables["ADAPTERS"], tables["KIND_OWNERS"]


def entries(files: Mapping[str, bytes]) -> dict[str, tuple[Entry, ...]]:
    """The hand-written entry table, with page kinds read from the captured registry."""
    adapters, owners = _registry(files)
    result: dict[str, tuple[Entry, ...]] = {}
    for kind, source in sorted(owners.items()):
        adapter = Entry(adapters[source].removeprefix("swingset."))
        result[f"{SCOPE_PREFIX}extract/{kind}"] = (
            Entry("sources.registry"),
            adapter,
            Entry("fetch.archive"),
        )
        result[f"{SCOPE_PREFIX}parse/{kind}"] = (
            Entry("sources.registry"),
            adapter,
            Entry("admission.contracts"),
            Entry("admission.report"),
            Entry("admission.generations"),
            Entry("model.observations"),
            Entry("schedule.parse", deep=False),
            *(Entry(module, deep=False) for module in PARSE_WRITERS),
        )
    for kind, module in sorted(PROJECTORS.items()):
        result[f"{SCOPE_PREFIX}project/{kind}"] = (
            Entry("project.process"),
            Entry("project.materialization"),
            Entry(module),
        )
    result[f"{SCOPE_PREFIX}link"] = (Entry("link.service"), Entry("link.persistence"))
    result[f"{SCOPE_PREFIX}build"] = (Entry("build.service"), Entry("build.generations"))
    return result


_CACHE: dict[str, dict[str, object]] = {}


def manifest(files: Mapping[str, bytes]) -> dict[str, object]:
    """`recipes/scopes.json`: each scope's files and the environment's files, by digest."""
    hashes = {name: digest(files[name]) for name in sorted(package_paths(files))}
    env = {name: digest(files[name]) for name in ENV_FILES if name in files}
    key = digest(canonical({"package": hashes, "env": env}))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    graph = _Graph(files)
    scopes: dict[str, dict[str, str]] = {}
    # Every scope is named, even an empty one in a partial package, so a
    # unit's recipe always carries its code identity (recipes.recipe_inputs
    # fails closed without it).
    for name, named in entries(files).items():
        scopes[name] = {path: hashes[path] for path in sorted(graph.closure(named))}
    result: dict[str, object] = {"format": FORMAT, "scopes": scopes, "env": env}
    if len(_CACHE) >= 8:
        _CACHE.clear()
    _CACHE[key] = result
    return result


def unattributed(files: Mapping[str, bytes]) -> set[str]:
    """Package paths that neither a scope nor `EXCLUDED` accounts for."""
    graph = _Graph(files)
    covered: set[str] = set()
    for named in entries(files).values():
        covered |= graph.closure(named)
    return {
        path
        for path in package_paths(files) - covered
        if not any(
            path[len(PREFIX) :] == item
            or (item.endswith("/") and path[len(PREFIX) :].startswith(item))
            for item in EXCLUDED
        )
    }
