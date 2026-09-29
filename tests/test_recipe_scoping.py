"""Scoped code recipes: each unit's recipe names only the code it can run (D-0207)."""

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

import swingset
from swingset.clock import FakeClock
from swingset.fetch.archive import canonical, digest
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state import inputs, recipe_scope
from swingset.state.db import open_database
from swingset.state.recipes import capture_runtime, captured_recipe_inputs

PREFIX = recipe_scope.PREFIX
START = "2010-01-01"
# Adapters that import another source's adapter on purpose: the newsletter
# reuses the council's date parsing, which reuses the calendar's.
SHARED = {
    "wsdc_newsletter": {"swingdancecouncil", "wsdc_calendar"},
    "swingdancecouncil": {"wsdc_calendar"},
}
# The shallow `schedule.parse` entry counts its own bytes only. If it starts to
# import something new, decide whether that code shapes parse output.
PARSE_DISPATCH_IMPORTS = {
    "swingset",
    "swingset.admission",
    "swingset.admission.contracts",
    "swingset.admission.generations",
    "swingset.admission.report",
    "swingset.admission.select",
    "swingset.admission.support",
    "swingset.clock",
    "swingset.fetch",
    "swingset.fetch.archive",
    "swingset.fetch.wayback",
    "swingset.history",
    "swingset.history.acquisition",
    "swingset.history.origin_dispatch",
    "swingset.sources",
    "swingset.sources.base",
    "swingset.state",
    "swingset.state.db",
    "swingset.state.findings",
    "swingset.state.observations",
    "swingset.state.verification",
    "swingset.state.work",
}


# Imports of `schedule.parse` that are in no parse scope: they dispatch or
# store work and shape no parse output.
DISPATCH_ONLY = {
    "swingset.clock": "the caller's clock",
    "swingset.state.db": "the connection and transactions",
    "swingset.state.work": "the queue and revision counters",
}
# Imports of a shallow parse module that fall outside some parse scope. Each
# was reviewed: it is storage, scheduling or a watch clock, not a writer of
# parse output. A new one needs the same review, or a `PARSE_WRITERS` entry.
SHALLOW_OUTSIDE = {
    "swingset.admission.select": {
        "swingset.state.db",
        "swingset.state.event_progress",
        "swingset.state.work",
    },
    "swingset.fetch.wayback": {"swingset.schedule.watches"},
    "swingset.history.acquisition": {"swingset.project", "swingset.project.history"},
    "swingset.history.origin_dispatch": {
        "swingset.clock",
        "swingset.config",
        "swingset.history.backfill",
        "swingset.history.captures",
        "swingset.history.origin",
        "swingset.history.platform",
        "swingset.schedule.watches",
        "swingset.state.control_scopes",
        "swingset.state.controls",
        "swingset.state.db",
    },
    "swingset.schedule.parse": set(DISPATCH_ONLY),
    "swingset.state": {"swingset.state.db"},
    "swingset.state.findings": {"swingset.state.retention", "swingset.state.work"},
    "swingset.state.observations": {"swingset.schedule.watches", "swingset.state.work"},
    "swingset.state.verification": {"swingset.state.db"},
}


def seal(files):
    """Add both recipe manifests, as `capture_runtime` does."""
    result = {name: body for name, body in files.items() if not name.startswith("recipes/")}
    result["recipes/runtime.json"] = canonical(
        {
            "format": "swingset-runtime-recipe-v1",
            "files": {name: digest(body) for name, body in sorted(result.items())},
        }
    )
    result["recipes/scopes.json"] = canonical(recipe_scope.manifest(result))
    return result


def code(files):
    """The code and environment digests a sealed bundle yields."""
    return {
        name: value
        for name, value in captured_recipe_inputs(files, history_start=START).items()
        if name.startswith("recipe/")
    }


def changed(before, after):
    return {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}


def edit(files, path, text="\n# edited\n"):
    return seal({**files, PREFIX + path: files.get(PREFIX + path, b"") + text.encode()})


@pytest.fixture(scope="module")
def package():
    """The real package, read once, with a fixed environment identity."""
    root = Path(swingset.__file__).parent
    files = {
        PREFIX + path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    files["runtime/environment.json"] = b'{"fixed":true}'
    return seal(files)


def test_every_package_file_is_in_a_scope_or_excluded_with_a_reason(package):
    assert recipe_scope.unattributed(package) == set()
    paths = {path.removeprefix(PREFIX) for path in recipe_scope.package_paths(package)}
    scoped = set()
    for entries in recipe_scope.entries(package).values():
        scoped |= {path.removeprefix(PREFIX) for path in recipe_scope.closure(package, entries)}
    for item, reason in recipe_scope.EXCLUDED.items():
        assert reason
        covered = {
            path for path in paths if path == item or (item.endswith("/") and path.startswith(item))
        }
        assert covered, f"excluded path does not exist: {item}"
        assert not covered & scoped, f"excluded path is in a scope: {item}"
    # Every entry module of the table exists, so no scope silently shrinks.
    modules = {path.removeprefix(PREFIX).removesuffix(".py").replace("/", ".") for path in package}
    for entries in recipe_scope.entries(package).values():
        for entry in entries:
            assert entry.module in modules or f"{entry.module}.__init__" in modules


def test_every_derivation_kind_names_a_scope_of_the_real_package(package):
    # recipe_inputs fails closed on a missing scope. Offline selection
    # contains that per kind; other readers of current() (the phase-two gate,
    # the build) would meet it too, so the real package names every scope.
    from swingset.state.derivation_dependencies import BASE
    from swingset.state.recipes import recipe_names

    scopes = set(recipe_scope.manifest(package)["scopes"])
    project = BASE | {"inventory", "mapping", "resolution", "event", "source_event", "year"}
    units = [("project", kind) for kind in sorted(project)]
    units += [("link", "event"), ("build", "release"), ("build", "correction")]
    for stage, kind in units:
        named = {
            name for name in recipe_names(stage, kind) if name.startswith(recipe_scope.SCOPE_PREFIX)
        }
        assert named and named <= scopes, (stage, kind)


def synthetic(tmp_path):
    """A small package shaped like the real one, captured from disk."""
    root = tmp_path / "project/src/swingset"
    tree = {
        "__init__.py": "",
        "fetch/__init__.py": "",
        "fetch/archive.py": "",
        "sources/__init__.py": "",
        "sources/base.py": "",
        "sources/registry.py": (
            "import importlib\n"
            "ADAPTERS: dict[str, str] = {'alpha': 'swingset.sources.alpha.adapter',"
            " 'beta': 'swingset.sources.beta.adapter'}\n"
            "KIND_OWNERS: dict[str, str] = {'alpha.page': 'alpha', 'alpha.list': 'alpha',"
            " 'beta.page': 'beta'}\n"
        ),
        "sources/alpha/__init__.py": "",
        "sources/alpha/adapter.py": (
            "from ..base import *\n"
            "def parse():\n"
            "    from swingset.sources.alpha import rules\n"
            "    return rules\n"
        ),
        "sources/alpha/rules.py": "",
        "sources/alpha/words.txt": "alpha\n",
        "sources/alpha/tables/names.csv": "name\n",
        "sources/alpha/fixtures/page.html": "<p>fixture</p>\n",
        "sources/beta/__init__.py": "",
        "sources/beta/adapter.py": "from swingset.sources.base import *\n",
        "admission/__init__.py": "",
        "admission/contracts.py": "from .report import *\n",
        "admission/report.py": "",
        "admission/generations.py": "",
        "model/__init__.py": "",
        "model/observations.py": "",
        "schedule/__init__.py": "",
        "schedule/parse.py": "from swingset.schedule.cycle import run\n",
        "schedule/cycle.py": "run = None\n",
    }
    for name, text in tree.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def test_function_local_imports_parent_packages_and_data_are_in_the_closure(tmp_path):
    files = capture_runtime(synthetic(tmp_path))
    paths = {
        path.removeprefix(PREFIX)
        for path in recipe_scope.closure(files, (recipe_scope.Entry("sources.alpha.adapter"),))
    }
    assert paths == {
        "__init__.py",
        "sources/__init__.py",
        "sources/base.py",
        "sources/alpha/__init__.py",
        "sources/alpha/adapter.py",
        "sources/alpha/rules.py",
        "sources/alpha/words.txt",
        "sources/alpha/tables/names.csv",
    }
    # The shallow dispatch entry adds its bytes and parents, not what it imports.
    shallow = {
        path.removeprefix(PREFIX)
        for path in recipe_scope.closure(files, (recipe_scope.Entry("schedule.parse", deep=False),))
    }
    assert shallow == {"__init__.py", "schedule/__init__.py", "schedule/parse.py"}
    names = code(files)
    assert {name for name in names if name.startswith("recipe/code/parse/")} == {
        "recipe/code/parse/alpha.list",
        "recipe/code/parse/alpha.page",
        "recipe/code/parse/beta.page",
    }


def test_a_fixture_edit_changes_no_recipe(package, tmp_path):
    fixture = next(path for path in package if path.startswith(PREFIX) and "/fixtures/" in path)
    edited = edit(package, fixture.removeprefix(PREFIX))
    assert edited["recipes/runtime.json"] != package["recipes/runtime.json"]
    assert changed(code(package), code(edited)) == set()
    # A new fixture file is not code either.
    added = seal({**package, PREFIX + "sources/eepro/fixtures/new.html": b"<p/>"})
    assert changed(code(package), code(added)) == set()


def test_a_parse_only_module_edit_changes_parse_recipes_and_no_extract_recipe(package):
    before = code(package)
    after = code(edit(package, "admission/removal.py"))
    difference = changed(before, after)
    parse = {name for name in before if name.startswith("recipe/code/parse/")}
    assert parse and parse <= difference
    assert not {name for name in difference if name.startswith("recipe/code/extract/")}
    assert "recipe/env" not in difference


def test_a_history_closure_edit_changes_nothing(package):
    assert changed(code(package), code(edit(package, "history/closure.py"))) == set()


# The scheduler, the loops and acquisition run stages; no derivation's output
# depends on their bytes, so no project, link or build scope names them (F4).
SCHEDULER = (
    "schedule/cycle.py",
    "schedule/derive.py",
    "schedule/offline.py",
    "schedule/fairness.py",
    "schedule/acquisition.py",
    "fetch/client.py",
    "history/backfill.py",
)


def test_the_scheduler_and_the_loops_are_in_no_scope(package):
    scopes = recipe_scope.manifest(package)["scopes"]
    assert isinstance(scopes, dict)
    loops = {
        path.removeprefix(PREFIX)
        for path in recipe_scope.package_paths(package)
        if path.startswith(PREFIX + "loops/")
    }
    assert "loops/runtime.py" in loops and "loops/release.py" in loops
    for name, paths in scopes.items():
        named = {path.removeprefix(PREFIX) for path in paths}
        assert not named & (loops | set(SCHEDULER)), (name, named & (loops | set(SCHEDULER)))
    # The build scope still holds what the build and its publication checks run.
    build = {path.removeprefix(PREFIX) for path in scopes["recipe/code/build"]}
    assert {"versions.py", "publish/safety.py", "build/closure.py"} <= build


@pytest.mark.parametrize("path", [*SCHEDULER, "loops/runtime.py", "loops/derive.py"])
def test_a_scheduler_or_loop_edit_changes_no_recipe(package, path):
    assert changed(code(package), code(edit(package, path))) == set()


def test_a_versions_edit_changes_only_project_link_and_build(package):
    difference = changed(code(package), code(edit(package, "versions.py")))
    expected = {
        name
        for name in code(package)
        if name.startswith(("recipe/code/project/", "recipe/code/link", "recipe/code/build"))
    }
    # Nine project kinds (the map split into mapping and resolution), link, build.
    assert len(expected) == 11
    assert difference == expected


def test_repository_identity_is_unchanged_by_the_move_to_versions(monkeypatch):
    import hashlib

    from swingset import versions
    from swingset.schedule import cycle

    monkeypatch.delenv("SWINGSET_REVISION", raising=False)
    assert cycle.repository_identity is versions.repository_identity
    assert cycle.versions is versions.versions
    # The digest root is still the package directory, as it was from
    # schedule/cycle.py, so an unchanged tree keeps its identity (D-0188).
    root = Path(cycle.__file__).resolve().parents[1]
    assert root == Path(swingset.__file__).resolve().parent
    expected = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix().encode()
        body = path.read_bytes()
        expected.update(len(relative).to_bytes(4, "big") + relative)
        expected.update(len(body).to_bytes(8, "big") + body)
    identity = f"source-sha256:{expected.hexdigest()}"
    assert versions.repository_identity() == identity
    assert versions.versions()["repository"] == identity


def _watch(conn, source, parser, number, now, run_id):
    spec = WatchSpec("", source, "round", "GET", f"https://{source}.example/{number}", parser)
    upsert_watch(conn, spec, now)
    conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
        "body_bytes,content_changed,run_id,classification,parse_status,parsed_at,parser_version) "
        "VALUES (?,?,'GET',?,?,200,0,1,?,'Ok','parsed',?,'1')",
        (
            f"{parser}-{number}",
            spec.watch_id,
            spec.url,
            now.isoformat(),
            run_id,
            now.isoformat(),
        ),
    )
    return spec.watch_id


def _accept(database, files, clock, monkeypatch):
    monkeypatch.setattr(inputs, "capture_runtime", lambda: files)
    bundle = inputs.capture(Path("config"), Path("overrides"), database.state_dir, {})
    return inputs.accept(database, bundle, clock)


def test_an_adapter_edit_requeues_only_its_kinds_and_a_versions_edit_no_parse(
    package, tmp_path, monkeypatch
):
    now = datetime(2026, 9, 25, tzinfo=UTC)
    clock = FakeClock(now)
    eepro = {"eepro.round", "eepro.index", "eepro.autoindex"}
    with open_database(tmp_path / "state") as database:
        conn = database.connection
        _accept(database, package, clock, monkeypatch)
        run_id = database.start_run(now)
        rounds = _watch(conn, "eepro", "eepro.round", 1, now, run_id)
        index = _watch(conn, "eepro", "eepro.index", 2, now, run_id)
        calendar = _watch(conn, "wsdc_calendar", "wsdc_calendar.events", 3, now, run_id)
        conn.execute("UPDATE watches SET extract_version='cached'")
        conn.execute("DELETE FROM pending_work")

        edited = edit(package, "sources/eepro/adapter.py")
        accepted = _accept(database, edited, clock, monkeypatch)
        assert accepted == {
            f"recipe/code/{stage}/{kind}" for stage in ("extract", "parse") for kind in eepro
        }
        versions = dict(conn.execute("SELECT watch_id,extract_version FROM watches"))
        assert versions[rounds] is None and versions[index] is None
        assert versions[calendar] == "cached"
        pending = {
            str(row[0])
            for row in conn.execute("SELECT unit_id FROM pending_work WHERE stage='parse'")
        }
        assert pending == {"eepro.round-1", "eepro.index-2"}

        # Acceptance: a code-only change to a non-parser module re-queues no parse.
        conn.execute("DELETE FROM pending_work")
        versions = edit(edited, "versions.py")
        accepted = _accept(database, versions, clock, monkeypatch)
        assert accepted and all(
            name.startswith(("recipe/code/project/", "recipe/code/link", "recipe/code/build"))
            for name in accepted
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM pending_work WHERE stage='parse'").fetchone()[0] == 0
        )
        # A cycle edit changes no recipe and queues nothing (F4).
        queued = conn.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0]
        assert _accept(database, edit(versions, "schedule/cycle.py"), clock, monkeypatch) == set()
        assert conn.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == queued


def test_no_parse_closure_reaches_another_sources_adapter(package):
    from swingset.sources.registry import ADAPTERS, KIND_OWNERS

    adapters = {
        source: PREFIX + module.removeprefix("swingset.").replace(".", "/") + ".py"
        for source, module in ADAPTERS.items()
    }
    scopes = recipe_scope.manifest(package)["scopes"]
    assert isinstance(scopes, dict)
    for kind, owner in KIND_OWNERS.items():
        for stage in ("extract", "parse"):
            reached = {
                source
                for source, path in adapters.items()
                if path in scopes[f"recipe/code/{stage}/{kind}"]
            }
            assert reached == {owner} | SHARED.get(owner, set()), (stage, kind)


def test_dynamic_imports_are_only_the_registry_and_the_cycle_clock(package):
    found = set()
    for path in sorted(recipe_scope.package_paths(package)):
        if not path.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(package[path])):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Name) and function.id == "__import__":
                argument = node.args[0]
                assert isinstance(argument, ast.Constant)
                found.add((path.removeprefix(PREFIX), argument.value))
            elif isinstance(function, ast.Attribute) and function.attr == "import_module":
                found.add((path.removeprefix(PREFIX), "import_module"))
    assert found == {("sources/registry.py", "import_module"), ("schedule/cycle.py", "datetime")}


def test_the_shallow_parse_dispatch_imports_are_pinned(package):
    assert recipe_scope.module_imports(package, "swingset.schedule.parse") == PARSE_DISPATCH_IMPORTS


def _parse_scopes(package):
    scopes = recipe_scope.manifest(package)["scopes"]
    assert isinstance(scopes, dict)
    return {name: set(paths) for name, paths in scopes.items() if "/parse/" in name}


def _in_scope(module, scope):
    parts = module.split(".")[1:]
    path = PREFIX + "/".join(parts)
    return path + ".py" in scope or path.rstrip("/") + "/__init__.py" in scope


def test_every_parse_dispatch_import_is_in_each_parse_scope_or_dispatch_only(package):
    imports = recipe_scope.module_imports(package, "swingset.schedule.parse")
    for name, scope in _parse_scopes(package).items():
        outside = {module for module in imports if not _in_scope(module, scope)}
        assert outside <= set(DISPATCH_ONLY), (name, outside - set(DISPATCH_ONLY))


def test_shallow_parse_modules_reach_outside_their_scope_only_where_reviewed(package):
    shallow = {
        "swingset.schedule.parse",
        *(f"swingset.{module}" for module in recipe_scope.PARSE_WRITERS),
    }
    modules = shallow | {
        ".".join(module.split(".")[:index])
        for module in shallow
        for index in range(1, module.count(".") + 1)
    }
    found: dict[str, set[str]] = {}
    for scope in _parse_scopes(package).values():
        for module in modules:
            for imported in recipe_scope.module_imports(package, module):
                if not _in_scope(imported, scope):
                    found.setdefault(module, set()).add(imported)
    assert found == SHALLOW_OUTSIDE


@pytest.mark.parametrize("module", recipe_scope.PARSE_WRITERS)
def test_a_parse_writer_edit_changes_every_parse_recipe_and_no_extract_recipe(package, module):
    before = code(package)
    difference = changed(before, code(edit(package, module.replace(".", "/") + ".py")))
    parse = {name for name in before if name.startswith("recipe/code/parse/")}
    assert parse and parse <= difference
    assert not {name for name in difference if name.startswith("recipe/code/extract/")}


def test_a_new_migration_changes_no_parse_recipe(package):
    added = seal({**package, PREFIX + "state/migrations/0099_later.sql": b"SELECT 1;\n"})
    difference = changed(code(package), code(added))
    assert difference and not {name for name in difference if "/parse/" in name}


def test_an_old_bundle_reproduces_its_runtime_recipe(package):
    old = {name: body for name, body in package.items() if name != "recipes/scopes.json"}
    names = captured_recipe_inputs(old, history_start=START)
    assert names["recipe/runtime"] == digest(old["recipes/runtime.json"])
    assert not any(name.startswith(("recipe/code/", "recipe/env")) for name in names)
    scoped = captured_recipe_inputs(package, history_start=START)
    assert "recipe/runtime" not in scoped and "recipe/env" in scoped
    # A scoped file edited without a new manifest is refused.
    tampered = dict(package)
    tampered[PREFIX + "sources/eepro/adapter.py"] += b"# unsealed\n"
    manifest = canonical(
        {
            "format": "swingset-runtime-recipe-v1",
            "files": {
                name: digest(body)
                for name, body in sorted(tampered.items())
                if name.startswith("runtime/")
            },
        }
    )
    tampered["recipes/runtime.json"] = manifest
    with pytest.raises(ValueError, match="scoped recipe"):
        captured_recipe_inputs(tampered, history_start=START)
