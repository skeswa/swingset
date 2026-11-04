"""Select the reviewed core by default; explicit paths and --full-suite run more."""

import os
import tomllib
from pathlib import Path

import pytest

pytest_plugins = ("swingset.dev.pytest_plugin", "swingset.dev.pytest_paths")


@pytest.fixture(scope="session")
def legacy_sqlite(tmp_path_factory):
    from legacy_sqlite import HistoricalSQLite

    return HistoricalSQLite(tmp_path_factory.mktemp("historical-sqlite"))


@pytest.fixture(scope="session", autouse=True)
def postgres_test_run():
    """Own test databases lazily; collecting or running pure tests needs no Docker."""
    from swingset.dev import testing

    previous_run = os.environ.get(testing.RUN_ENV)
    previous_owner = os.environ.get(testing.OWNER_ENV)
    isolated_variables = (
        "DATABASE_URL",
        "SWINGSET_DATABASE_URL",
        "SWINGSET_CONTROL_DATABASE_URL",
        "SWINGSET_OWNER_DATABASE_URL",
        "SWINGSET_SCRATCH_ADMIN_DSN",
        "SWINGSET_ENV",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
    )
    previous_environment = {
        name: os.environ.pop(name) for name in isolated_variables if name in os.environ
    }
    identifier = testing.begin_run()
    os.environ[testing.RUN_ENV] = identifier
    os.environ.pop(testing.OWNER_ENV, None)
    try:
        yield identifier
    finally:
        testing.finish_run(identifier)
        if previous_run is None:
            os.environ.pop(testing.RUN_ENV, None)
        else:
            os.environ[testing.RUN_ENV] = previous_run
        if previous_owner is not None:
            os.environ[testing.OWNER_ENV] = previous_owner
        for name in isolated_variables:
            os.environ.pop(name, None)
        os.environ.update(previous_environment)


@pytest.fixture(autouse=True)
def postgres_test_owner(postgres_test_run, request, monkeypatch):
    from swingset.dev import testing

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SWINGSET_DATABASE_URL", raising=False)
    monkeypatch.delenv("SWINGSET_CONTROL_DATABASE_URL", raising=False)
    monkeypatch.delenv("SWINGSET_OWNER_DATABASE_URL", raising=False)
    monkeypatch.delenv("SWINGSET_SCRATCH_ADMIN_DSN", raising=False)
    monkeypatch.delenv("SWINGSET_ENV", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv(testing.OWNER_ENV, request.node.nodeid)
    try:
        yield
    finally:
        testing.finish_test(postgres_test_run, request.node.nodeid)


@pytest.fixture(autouse=True)
def no_worker_quota(monkeypatch, tmp_path_factory):
    """Seal tests off from the worker's real quota reading (`/run/swingset-disk`):
    a stale or low reading there would fail room checks for reasons that have
    nothing to do with the code. A test that needs a quota sets one itself."""
    from swingset.backup import drill
    from swingset.loops import release
    from swingset.state import disk

    absent = tmp_path_factory.getbasetemp() / "no-swingset-disk"
    monkeypatch.setattr(disk, "QUOTA_FILE", absent)
    monkeypatch.setattr(release, "DISK_FILE", absent)
    monkeypatch.setattr(drill, "QUOTA_CACHE", absent)


def pytest_addoption(parser):
    parser.addoption(
        "--full-suite",
        action="store_true",
        help="Run core and extended tests, including exhaustive crash recovery.",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "core: high-impact default regression coverage")
    config.addinivalue_line("markers", "extended: specialist or exhaustive regression coverage")


def matches(nodeid, selector):
    return (
        nodeid == selector
        or nodeid.startswith(selector + "::")
        or nodeid.startswith(selector + "[")
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    suite = tomllib.loads(Path(__file__).with_name("suite.toml").read_text())
    core = suite["core"]
    default = (
        not config.getoption("full_suite") and config.args_source != pytest.Config.ArgsSource.ARGS
    )
    if default:
        known = {selector.split("::")[0] for selector in core} | set(suite["extended"])
        unknown = {item.nodeid.split("::")[0] for item in items} - known
        # --lf prunes previously passing files before this hook runs.
        stale = (
            set()
            if config.getoption("lf")
            else {
                selector
                for selector in core
                if not any(matches(item.nodeid, selector) for item in items)
            }
        )
        if unknown or stale:
            raise pytest.UsageError(
                "Update tests/suite.toml before running the default suite. "
                f"Unclassified files: {sorted(unknown)}; unmatched core selectors: {sorted(stale)}. "
                "Explicit test paths or --full-suite still run all requested tests."
            )

    selected, deselected = [], []
    for item in items:
        if any(matches(item.nodeid, selector) for selector in suite.get("capacity_disk", [])):
            item.add_marker("capacity_disk")
        is_core = not item.get_closest_marker("extended") and (
            item.get_closest_marker("core")
            or any(matches(item.nodeid, selector) for selector in core)
        )
        item.add_marker("core" if is_core else "extended")
        (deselected if default and not is_core else selected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
    if default and (
        getattr(config.option, "numprocesses", 0)
        or hasattr(config, "workerinput")
        or config.getoption("swingset_core_phase", default=None) == "prologue"
    ):
        # The armed acceptance subprocess is one measured long file group;
        # start it first while retaining every selected item and file affinity.
        items.sort(key=lambda item: item.nodeid.split("::", 1)[0] != "tests/test_read_sets.py")
