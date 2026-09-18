"""Select the reviewed core by default; explicit paths and --full-suite run more."""

import tomllib
from pathlib import Path

import pytest


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
        is_core = not item.get_closest_marker("extended") and (
            item.get_closest_marker("core")
            or any(matches(item.nodeid, selector) for selector in core)
        )
        item.add_marker("core" if is_core else "extended")
        (deselected if default and not is_core else selected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
