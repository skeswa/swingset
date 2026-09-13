"""Capture the runtime artifact independently of human version labels."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sqlite3
import sys
import sysconfig
from collections.abc import Mapping
from pathlib import Path

from swingset.fetch.archive import canonical, digest


def environment_identity() -> dict[str, object]:
    """Identify the interpreter and installed dependency manifests.

    Distribution RECORD digests describe installed wheel contents. They are
    dependency identities, not an audit of every installed binary on each cycle.
    The project's own executed source is captured separately, byte for byte.
    """
    distributions = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name", "").casefold().replace("_", "-")
        if name == "swingset":
            continue
        record = distribution.read_text("RECORD")
        distributions.append(
            {
                "name": name,
                "version": distribution.version,
                "record_sha256": digest(record.encode()) if record is not None else None,
            }
        )
    return {
        "implementation": sys.implementation.name,
        "version": platform.python_version(),
        "compiler": platform.python_compiler(),
        "abi": sysconfig.get_config_var("SOABI"),
        "executable_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
        "distributions": sorted(
            distributions,
            key=lambda row: (row["name"] or "", row["version"] or "", row["record_sha256"] or ""),
        ),
    }


def capture_runtime(package_root: Path | None = None) -> dict[str, bytes]:
    """Retain code, schemas, vocabularies, lock inputs, and their exact manifest."""
    root = package_root or Path(__file__).resolve().parents[1]
    files = {
        "runtime/swingset/" + path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    if not files or "runtime/swingset/__init__.py" not in files:
        raise ValueError("runtime package is missing its source artifact")
    project = root.parent.parent
    for name in ("pyproject.toml", "uv.lock"):
        path = project / name
        if path.is_file():
            files["runtime/" + name] = path.read_bytes()
    files["runtime/environment.json"] = canonical(environment_identity())
    files["recipes/runtime.json"] = canonical(
        {
            "format": "swingset-runtime-recipe-v1",
            "files": {name: digest(body) for name, body in sorted(files.items())},
        }
    )
    return files


def captured_recipe_inputs(files: Mapping[str, bytes], *, history_start: str) -> dict[str, str]:
    """Reduce captured artifacts to accepted semantic inputs, excluding scheduling."""
    raw = files.get("recipes/runtime.json")
    result = {"policy/history_start": digest(canonical(history_start))}
    if raw is None:
        return result
    manifest = json.loads(raw)
    if manifest.get("format") != "swingset-runtime-recipe-v1":
        raise ValueError("unknown captured runtime recipe")
    expected = manifest.get("files")
    actual = {name: digest(body) for name, body in files.items() if name.startswith("runtime/")}
    if not isinstance(expected, dict) or expected != actual:
        raise ValueError("captured runtime artifact differs from its recipe")
    result["recipe/runtime"] = digest(raw)
    return result


def recipe_inputs(conn: sqlite3.Connection, stage: str) -> dict[str, str]:
    """Return accepted execution and policy identities for one derivation stage."""
    common = {"recipe/runtime", "policy/history_start"}
    selected = {
        "project": common
        | {
            "policy/inventory_year",
            "overrides/event_aliases.csv",
            "overrides/source_urls.csv",
            "overrides/series_aliases.csv",
        },
        "link": common
        | {"link/weights.toml", "overrides/nicknames.csv", "overrides/identity_overrides.csv"},
        "build": common | {"overrides/suppressions.csv", "overrides/identity_overrides.csv"},
        "parse": common,
    }
    if stage not in selected:
        raise ValueError(f"unknown recipe stage: {stage}")
    return {
        str(name): str(value)
        for name, value in conn.execute(
            "SELECT input_name,digest FROM accepted_inputs WHERE consumer='pipeline' ORDER BY input_name"
        )
        if name in selected[stage]
    }
