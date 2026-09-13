"""Captured policy identity excludes acquisition-only scheduling controls."""

from pathlib import Path

from swingset.clock import FakeClock
from swingset.state import inputs, recipes
from swingset.state.db import open_database


def test_runtime_bytes_are_retained_once_as_one_accepted_recipe(tmp_path, monkeypatch):
    package = tmp_path / "project/src/swingset"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n")
    artifact = recipes.capture_runtime(package)
    monkeypatch.setattr(inputs, "capture_runtime", lambda: artifact)
    with open_database(tmp_path / "state") as db:
        bundle = inputs.capture(Path("config"), Path("overrides"), db.state_dir, {})
        inputs.accept(db, bundle, FakeClock())
        assert (bundle.path / "runtime/swingset/__init__.py").read_bytes() == b"VALUE = 1\n"
        accepted = dict(db.connection.execute("SELECT input_name,digest FROM accepted_inputs"))
        assert accepted["recipe/runtime"] == bundle.file_hashes["recipes/runtime.json"]
        assert not any(name.startswith("runtime/") for name in accepted)
        assert not any(name.startswith("recipes/") for name in accepted)
        project = recipes.recipe_inputs(db.connection, "project")
        assert "config/sources.toml" not in project
        assert "config/hosts.toml" not in project
        assert project["policy/history_start"] == accepted["policy/history_start"]
        assert not inputs.accept(db, bundle, FakeClock())
