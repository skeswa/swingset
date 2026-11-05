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

`DISPATCH` modules are literal tables that import one module by name:
`sources.registry` resolves a kind's adapter and `admission.kinds` its
contract module. The walk stops at them: a scope that reaches one holds not
its bytes but `<path>#code`, the digest of its code with the table values
blanked (`dispatch_code`), and none of its imports. The extract and parse
entries name what the tables resolve for their kind instead, read from the
captured literals: the owning adapter and the kind's contract module
(D-0261). So a lookup edit re-runs every scope that reaches the module, a new
registration changes only the new kinds' scopes, and an edit to one kind's
contract module changes only the parse scopes of the kinds that module lists.
A table edit re-runs no other reader: the build's per-source coverage rows
and the phase-two gate's contract version for a child kind read the tables
without their entries in scope, so a new registration reaches the next
release build, not earlier ones.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
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

# Dispatch tables the walk stops at (see the module docstring). A test pins
# their imports, their lookups and the digest of their code.
CODE_SUFFIX = "#code"
DISPATCH: dict[str, str] = {
    "admission.kinds": "contract dispatch; each parse scope names its kind's contract module",
    "sources.registry": "adapter dispatch; each extract and parse scope names its kind's adapter",
}

_ACQUIRE = "acquisition; it fetches, gates and stores pages that parse reads"
_EVENTS = "event acquisition bookkeeping; the plan loop runs it"
_SCHEDULE = "scheduling; it runs stages through the modules the scopes name"
# Package paths no recipe names. A path ending in "/" covers its directory.
# Since `versions.py` left the scheduler, no project, link or build closure
# reaches the scheduler or acquisition (architecture plan, F4).
EXCLUDED: dict[str, str] = {
    "ops/": "read-only operator reports and health checks",
    "dev/": "local development services, fixture seeds and scratch replay",
    "state/migration_job.py": "explicit operator migration envelope",
    "state/postgres_rebuild.py": "explicit restore bootstrap of discarded materializations",
    "state/postgres_sample.py": "read-only bounded legacy sampler",
    "state/postgres_import.py": "operator import of frozen SQLite checkpoints",
    "state/legacy_checkpoint.py": "read-only validation of legacy import files",
    "state/legacy_restore.py": "offline extraction and validation of frozen SQLite transport",
    "deploy/": "service scheduling, network policy and external rehearsal adapters; no derivation output",
    "admission/activation.py": "operator command that activates or pauses a reviewed contract",
    "admission/corpus.py": "offline admission corpus export",
    "admission/enumeration_evidence.py": _EVENTS,
    "admission/event_declarations.py": _EVENTS,
    "admission/event_retirement.py": _EVENTS,
    "admission/parent_evidence.py": _EVENTS,
    "admission/policy.py": "operator commands that change admission policy rows",
    "admission/relabel.py": "operator command that stores accepted results again (D-0241)",
    "publish/service.py": "publication orchestration and its pre-push backup; derivations read only journal metadata",
    "publish/huggingface.py": "external dataset repository transport",
    "state/history_catalog.py": "filtered backup classification and exact restore audit",
    "state/postgres_backup.py": "operator history COPY backup and restore",
    "state/postgres_copy.py": "isolated verification database copies",
    "state/postgres_copy_codec.py": "offline validation of retained binary COPY streams",
    "state/postgres_files.py": "backup file closure qualification",
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
    "schedule/release_steps.py": "release build and publish steps; the release loop and the CLI run them",
    "state/acquisition_plan.py": "loop plan rows; acquisition reads them, no derivation does",
    "state/closure_collection.py": "operator retention commands",
    "state/collector.py": "the mark-sweep collector and maintain's artifact pass; no derivation reads it",
    "state/control_scopes.py": "control dependencies of admission and status; the stages check controls, not outputs",
    "state/control_time.py": "operator control commands",
    "state/disk.py": "free space and delete settings for storage, backups and reclaim; no derivation reads it",
    "state/event_progress.py": _EVENTS,
    "state/guards.py": "fetch guard reasons; acquisition writes them, no derivation reads them",
    "state/lag_report.py": "status report",
    "state/loop_state.py": "loop tick status; reports read it, no derivation does",
    "state/derivation_readiness.py": (
        "bulk dancer currentness; no derivation calls it since link read sets (plan F20)"
    ),
    "state/publication_report.py": "status report",
    "state/requirement_report.py": "status report",
    "state/requirement_scopes.py": "requirement scan pages; acquisition runs the scan, no derivation does",
    "state/requirements.py": "requirement scan; acquisition runs it, no derivation does (D-0237)",
    "state/retention_apply.py": "operator retention commands",
    "state/storage.py": "operator storage move and the maintain evictor; no derivation reads it",
    "state/verify_graph.py": "offline proof on isolated checkpoint copies",
    "state/rekey.py": "operator command that records proven recipe equivalences",
    "state/release_verification.py": "release authorization from complete differential proof receipts",
    "state/verify.py": "weekly verify sample; it re-runs units and rolls every write back",
    "state/legacy_recipes.py": "operator backup and re-key archive closure; resolves retained code witnesses without executing them",
}


def dispatch_code(body: bytes) -> str:
    """Digest of a dispatch module's code with each literal table's values blanked."""
    tree = ast.parse(body)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign | ast.Assign) and isinstance(node.value, ast.Dict):
            node.value = ast.Dict(keys=[], values=[])
    return digest(ast.dump(tree).encode())


def member_digest(files: Mapping[str, bytes], member: str) -> str | None:
    """The digest a scope records for one member: a file's bytes or a dispatch module's code."""
    path, _, suffix = member.partition("#")
    body = files.get(path)
    if body is None or (suffix and "#" + suffix != CODE_SUFFIX):
        return None
    return dispatch_code(body) if suffix else digest(body)


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
        self.stops = {f"swingset.{module}" for module in DISPATCH}
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
            # The explicit pytest-only provisioning hook never runs a transform.
            # Captured runtime artifacts still retain its exact source bytes.
            if module == "swingset.state.engine.settings" and name.startswith(
                "swingset.dev.testing"
            ):
                continue
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
        stopped: set[str] = set()

        def walk(stack: list[str]) -> None:
            while stack:
                module = stack.pop()
                if module in self.stops:
                    stopped.add(module)
                    continue
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
        paths.update(self.modules[module] + CODE_SUFFIX for module in stopped)
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


def _tables(files: Mapping[str, bytes], path: str, *names: str) -> dict[str, dict[str, str]]:
    """Read literal dict tables from a captured dispatch module; absent ones are empty."""
    body = files.get(PREFIX + path)
    tables: dict[str, dict[str, str]] = {name: {} for name in names}
    if body is None:
        return tables
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
    return tables


def entries(files: Mapping[str, bytes]) -> dict[str, tuple[Entry, ...]]:
    """The hand-written entry table, with page kinds read from the captured dispatch tables."""
    registry = _tables(files, "sources/registry.py", "ADAPTERS", "KIND_OWNERS")
    adapters, owners = registry["ADAPTERS"], registry["KIND_OWNERS"]
    contracts = _tables(files, "admission/kinds.py", "CONTRACTS")["CONTRACTS"]
    result: dict[str, tuple[Entry, ...]] = {}
    for kind, source in sorted(owners.items()):
        adapter = Entry(adapters[source].removeprefix("swingset."))
        contract = contracts.get(kind)
        result[f"{SCOPE_PREFIX}extract/{kind}"] = (adapter, Entry("fetch.archive"))
        result[f"{SCOPE_PREFIX}parse/{kind}"] = (
            adapter,
            *((Entry(contract.removeprefix("swingset.")),) if contract else ()),
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


def _literals(body: bytes) -> dict[str, object]:
    """Read declarations without importing or executing captured source."""
    module = ast.parse(body)
    bound: set[str] = set()
    for statement in module.body:
        if isinstance(statement, ast.Assign | ast.AnnAssign):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            bound.update(
                node.id
                for target in targets
                for node in ast.walk(target)
                if isinstance(node, ast.Name)
            )
        elif isinstance(statement, ast.Import | ast.ImportFrom):
            bound.update(alias.asname or alias.name.split(".")[0] for alias in statement.names)
        elif isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(statement.name)
    result: dict[str, object] = {}
    for node in module.body:
        target = (
            node.target
            if isinstance(node, ast.AnnAssign)
            else node.targets[0]
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            else None
        )
        value = getattr(node, "value", None)
        if isinstance(target, ast.Name) and value is not None:
            try:
                result[target.id] = ast.literal_eval(value)
            except (ValueError, TypeError):
                # Historical contracts spelled their literal table as
                # KINDS=frozenset({...}); VERSIONS=dict.fromkeys(sorted(KINDS),'1').
                # Recognize only those exact constructors, with unshadowed
                # builtins and already captured string keys. Never eval source.
                if {"frozenset", "sorted", "dict"} & bound:
                    continue
                if (
                    target.id == "KINDS"
                    and isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "frozenset"
                    and len(value.args) == 1
                    and not value.keywords
                ):
                    try:
                        keys = ast.literal_eval(value.args[0])
                    except (ValueError, TypeError):
                        continue
                    if isinstance(keys, (set, tuple, list)) and all(
                        isinstance(key, str) for key in keys
                    ):
                        result[target.id] = frozenset(keys)
                elif (
                    target.id == "VERSIONS"
                    and isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "fromkeys"
                    and isinstance(value.func.value, ast.Name)
                    and value.func.value.id == "dict"
                    and len(value.args) == 2
                    and not value.keywords
                ):
                    key_expression = value.args[0]
                    if not (
                        isinstance(key_expression, ast.Call)
                        and isinstance(key_expression.func, ast.Name)
                        and key_expression.func.id == "sorted"
                        and len(key_expression.args) == 1
                        and not key_expression.keywords
                        and isinstance(key_expression.args[0], ast.Name)
                        and key_expression.args[0].id == "KINDS"
                    ):
                        continue
                    keys = result.get("KINDS")
                    try:
                        version = ast.literal_eval(value.args[1])
                    except (ValueError, TypeError):
                        continue
                    if (
                        isinstance(keys, (frozenset, set, tuple, list))
                        and all(isinstance(key, str) for key in keys)
                        and isinstance(version, (str, int))
                        and not isinstance(version, bool)
                    ):
                        result[target.id] = dict.fromkeys(sorted(keys), version)
    return result


@lru_cache(maxsize=1024)
def _pages(body: bytes) -> tuple[dict[str, object], ...]:
    return tuple(
        _literals("\n".join(ast.unparse(n) for n in node.body).encode())
        for node in ast.parse(body).body
        if isinstance(node, ast.ClassDef)
    )


def declared_versions(files: Mapping[str, bytes]) -> dict[str, dict[str, object]]:
    """Transform versions from captured declarations, never live imports.

    Adapter declarations belong to the class with the requested literal kind.
    Shared parse writers use PARSE_CORE_VERSION; they have no separate knobs.
    """
    registry = _tables(files, "sources/registry.py", "ADAPTERS", "KIND_OWNERS")
    contracts = _tables(files, "admission/kinds.py", "CONTRACTS")["CONTRACTS"]
    result: dict[str, dict[str, object]] = {}
    graph = _Graph(files)
    literal_values: dict[bytes, dict[str, object]] = {}

    def literals(body: bytes) -> dict[str, object]:
        if body not in literal_values:
            literal_values[body] = _literals(body)
        return literal_values[body]

    def constant(module: str, name: str) -> object:
        body = files.get(PREFIX + module.replace(".", "/") + ".py")
        if body is None:
            return None  # partial artifacts used by offline helpers
        values = literals(body)
        if name not in values:
            raise ValueError(f"missing literal declared version {module}:{name}")
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError(f"invalid declared version {module}:{name}")
        return value

    for kind, owner in sorted(registry["KIND_OWNERS"].items()):
        module = registry["ADAPTERS"][owner].removeprefix("swingset.")
        matches = []
        for path in sorted(graph.closure((Entry(module),))):
            if not path.endswith(".py"):
                continue
            for page in _pages(files[path]):
                if page.get("kind") == kind:
                    matches.append((path, page))
        if len(matches) != 1:
            raise ValueError(f"missing or duplicate page declaration for {kind}")
        page_path, page = matches[0]
        module = page_path.removeprefix(PREFIX).removesuffix(".py").replace("/", ".")
        for stage, label in (("extract", "EXTRACT_VERSION"), ("parse", "PARSER_VERSION")):
            value = page.get(label)
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError(f"missing literal declared version {module}:{kind}:{label}")
            versions: dict[str, object] = {module: value}
            if stage == "parse":
                versions["admission.contracts"] = constant(
                    "admission.contracts", "PARSE_CORE_VERSION"
                )
                if contract := contracts.get(kind):
                    contract = contract.removeprefix("swingset.")
                    values = literals(files[PREFIX + contract.replace(".", "/") + ".py"])
                    declared = values.get("VERSIONS")
                    if not isinstance(declared, dict) or kind not in declared:
                        raise ValueError(f"missing literal contract version {contract}:{kind}")
                    versions[contract] = declared[kind]
            result[f"{SCOPE_PREFIX}{stage}/{kind}"] = dict(sorted(versions.items()))
    for kind, module in sorted(PROJECTORS.items()):
        versions = {
            "project.process": constant("project.process", "PROJECTOR_VERSION"),
            "project.materialization": constant("project.materialization", "VERSION"),
        }
        if module != "project.process":
            versions[module] = constant(module, "VERSION")
        result[f"{SCOPE_PREFIX}project/{kind}"] = dict(sorted(versions.items()))
    result[f"{SCOPE_PREFIX}link"] = {
        "link.service": constant("link.service", "LINKER_VERSION"),
        "link.persistence": constant("link.persistence", "VERSION"),
    }
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
        scopes[name] = {
            path: hashes[path]
            if path in hashes
            else dispatch_code(files[path[: -len(CODE_SUFFIX)]])
            for path in sorted(graph.closure(named))
        }
    result: dict[str, object] = {
        "format": FORMAT,
        "scopes": scopes,
        "env": env,
        "declared": declared_versions(files),
    }
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
    covered |= {
        path for name, path in graph.modules.items() if name.removeprefix("swingset.") in DISPATCH
    }
    return {
        path
        for path in package_paths(files) - covered
        if not any(
            path[len(PREFIX) :] == item
            or (item.endswith("/") and path[len(PREFIX) :].startswith(item))
            for item in EXCLUDED
        )
    }
