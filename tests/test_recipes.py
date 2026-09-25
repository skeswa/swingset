"""Captured policy identity excludes acquisition-only scheduling controls."""

from pathlib import Path

import pytest

from swingset.admission.generations import parse_inputs
from swingset.clock import FakeClock
from swingset.fetch.archive import canonical, digest
from swingset.state import inputs, recipes
from swingset.state.db import open_database


def test_only_the_runtime_parts_of_pyproject_and_the_lock_change_the_environment(tmp_path):
    """A `[tool.*]` edit or a lock change (a dev tool bump) would re-parse every
    snapshot if `recipe/env` digested the whole files. It reads the installed
    distributions and pyproject's `[project]` table; both files stay in the
    bundle."""
    project = tmp_path / "project"
    package = project / "src/swingset"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n")
    pyproject = project / "pyproject.toml"
    pyproject.write_text('[project]\nname = "swingset"\ndependencies = ["a"]\n')
    lock = project / "uv.lock"
    lock.write_text("version = 1\n")

    def env() -> str:
        files = recipes.capture_runtime(package)
        assert "runtime/pyproject.toml" in files and "runtime/uv.lock" in files
        return recipes.captured_recipe_inputs(files, history_start="2010-01-01")["recipe/env"]

    first = env()
    pyproject.write_text(
        '[project]\nname = "swingset"\ndependencies = ["a"]\n[tool.ruff]\nline-length = 99\n'
    )
    lock.write_text('version = 1\n[[package]]\nname = "ruff"\nversion = "0.99"\n')
    assert env() == first
    pyproject.write_text('[project]\nname = "swingset"\ndependencies = ["a", "b"]\n')
    assert env() != first


def test_runtime_bytes_are_retained_once_as_scoped_recipes(tmp_path, monkeypatch):
    package = tmp_path / "project/src/swingset"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n")
    artifact = recipes.capture_runtime(package)
    monkeypatch.setattr(inputs, "capture_runtime", lambda: artifact)
    with open_database(tmp_path / "state") as db:
        bundle = inputs.capture(Path("config"), Path("overrides"), db.state_dir, {})
        inputs.accept(db, bundle, FakeClock())
        assert (bundle.path / "runtime/swingset/__init__.py").read_bytes() == b"VALUE = 1\n"
        assert (bundle.path / "recipes/scopes.json").is_file()
        accepted = dict(db.connection.execute("SELECT input_name,digest FROM accepted_inputs"))
        env = {
            name: bundle.file_hashes[name]
            for name in ("runtime/environment.json", "runtime/project.json")
            if name in bundle.file_hashes
        }
        assert accepted["recipe/env"] == digest(canonical(env))
        assert "recipe/runtime" not in accepted
        assert not any(name.startswith("runtime/") for name in accepted)
        assert not any(name.startswith("recipes/") for name in accepted)
        project = recipes.recipe_inputs(db.connection, "project", "event")
        assert "config/sources.toml" not in project
        assert "config/hosts.toml" not in project
        assert project["policy/history_start"] == accepted["policy/history_start"]
        assert project["recipe/env"] == accepted["recipe/env"]
        assert not inputs.accept(db, bundle, FakeClock())


def test_each_unit_selects_only_its_own_code_scope(tmp_path):
    names = {
        "recipe/env": "env",
        "policy/history_start": "start",
        "recipe/code/project/event": "event",
        "recipe/code/project/dancer": "dancer",
        "recipe/code/link": "link",
        "recipe/code/build": "build",
        "recipe/code/extract/eepro.round": "extract-round",
        "recipe/code/parse/eepro.round": "parse-round",
        "recipe/code/parse/eepro.index": "parse-index",
        "version/extract/eepro.round": "4",
        "version/parser/eepro.round": "7",
        "version/parser/eepro.index": "2",
        "version/repository": "source-sha256:any",
        "overrides/event_aliases.csv": "aliases",
    }
    with open_database(tmp_path) as db:
        db.connection.executemany(
            "INSERT INTO accepted_inputs VALUES ('pipeline',?,?)", names.items()
        )
        conn = db.connection
        event = recipes.recipe_inputs(conn, "project", "event")
        assert event["recipe/code/project/event"] == "event"
        assert "recipe/code/project/dancer" not in event
        assert "overrides/event_aliases.csv" in event
        assert set(recipes.recipe_inputs(conn, "link")) == {
            "recipe/env",
            "policy/history_start",
            "recipe/code/link",
        }
        assert "recipe/code/build" in recipes.recipe_inputs(conn, "build")
        parse = recipes.recipe_inputs(conn, "parse", "eepro.round")
        assert set(parse) == {
            "recipe/env",
            "policy/history_start",
            "recipe/code/extract/eepro.round",
            "recipe/code/parse/eepro.round",
        }
        with pytest.raises(ValueError, match="page kind"):
            recipes.recipe_inputs(conn, "parse")
        # Source-generation recipes add the kind's two labels and nothing else.
        assert parse_inputs(conn, "eepro.round") == tuple(
            ("pipeline", name, names[name])
            for name in sorted(
                {*parse, "version/extract/eepro.round", "version/parser/eepro.round"}
            )
        )


def test_a_unit_without_an_accepted_code_scope_fails_closed(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.executemany(
            "INSERT INTO accepted_inputs VALUES ('pipeline',?,?)",
            (("recipe/env", "env"), ("recipe/code/project/event", "event")),
        )
        assert "recipe/code/project/event" in recipes.recipe_inputs(conn, "project", "event")
        # A kind with no scope would carry no code identity and never re-run.
        with pytest.raises(ValueError, match="no code scope recipe/code/project/inventory"):
            recipes.recipe_inputs(conn, "project", "inventory")
        with pytest.raises(ValueError, match="no code scope recipe/code/link"):
            recipes.recipe_inputs(conn, "link")
        # A helper bundle names no kind and no code.
        assert "recipe/env" in recipes.recipe_inputs(conn, "project")


def test_every_project_unit_kind_has_a_projector_scope():
    from swingset.state.derivation_dependencies import BASE
    from swingset.state.recipe_scope import PROJECTORS

    assert set(PROJECTORS) == BASE | {"inventory", "map", "event", "source_event", "history"}
