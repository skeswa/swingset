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

from . import recipe_scope


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
    if "runtime/pyproject.toml" in files:
        import tomllib

        # What `recipe/env` reads of pyproject (recipe_scope.ENV_FILES).
        table = tomllib.loads(files["runtime/pyproject.toml"].decode()).get("project", {})
        files["runtime/project.json"] = canonical(table)
    files["runtime/environment.json"] = canonical(environment_identity())
    files["recipes/runtime.json"] = canonical(
        {
            "format": "swingset-runtime-recipe-v1",
            "files": {name: digest(body) for name, body in sorted(files.items())},
        }
    )
    files["recipes/scopes.json"] = canonical(recipe_scope.manifest(files))
    return files


def captured_recipe_inputs(files: Mapping[str, bytes], *, history_start: str) -> dict[str, str]:
    """Reduce captured artifacts to accepted semantic inputs, excluding scheduling.

    A bundle with `recipes/scopes.json` yields `recipe/env` and one digest per
    code scope (D-0207). An older bundle yields its package-wide
    `recipe/runtime`, so retained bundles still reproduce their recipe.
    """
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
    scoped = files.get("recipes/scopes.json")
    if scoped is None:
        result["recipe/runtime"] = digest(raw)
        return result
    scopes = json.loads(scoped)
    if scopes.get("format") != recipe_scope.FORMAT:
        raise ValueError("unknown captured scoped recipe")
    env, members = scopes.get("env"), scopes.get("scopes")
    if not isinstance(env, dict) or not isinstance(members, dict):
        raise ValueError("captured scoped recipe is malformed")
    for name, group in (("recipe/env", env), *members.items()):
        if name != "recipe/env" and not name.startswith(recipe_scope.SCOPE_PREFIX):
            raise ValueError(f"captured scoped recipe names a non-code input: {name}")
        if not isinstance(group, dict) or any(
            path not in files or digest(files[path]) != value for path, value in group.items()
        ):
            raise ValueError("captured runtime artifact differs from its scoped recipe")
        result[name] = digest(canonical(group))
    return result


# `recipe/runtime` is the package-wide identity of bundles captured before
# D-0207. After the first scoped bundle it stays at the absent-input digest.
_COMMON = frozenset({"recipe/env", "recipe/runtime", "policy/history_start"})


def recipe_names(stage: str, kind: str | None = None) -> frozenset[str]:
    """Accepted input names one derivation or parse unit depends on.

    `kind` is the project unit kind or the parse page kind. A project call
    without a kind omits the code scope; only helper bundles, which carry no
    runtime, make it.
    """
    code = recipe_scope.SCOPE_PREFIX
    if stage == "project":
        return _COMMON | {
            "policy/inventory_year",
            "overrides/event_aliases.csv",
            "overrides/source_urls.csv",
            "overrides/series_aliases.csv",
            "overrides/finding_reviews.csv",
            *((f"{code}project/{kind}",) if kind is not None else ()),
        }
    if stage == "link":
        return _COMMON | {
            "link/weights.toml",
            "overrides/nicknames.csv",
            "overrides/identity_overrides.csv",
            f"{code}link",
        }
    if stage == "build":
        return _COMMON | {
            "overrides/suppressions.csv",
            "overrides/identity_overrides.csv",
            f"{code}build",
        }
    if stage == "parse":
        if kind is None:
            raise ValueError("a parse recipe needs its page kind")
        from swingset.admission.generations import parse_recipe_names

        return parse_recipe_names(kind)
    raise ValueError(f"unknown recipe stage: {stage}")


class RecipeScopeMissing(ValueError):
    """An accepted scoped runtime names no code scope for a derivation kind."""


def recipe_inputs(conn: sqlite3.Connection, stage: str, kind: str | None = None) -> dict[str, str]:
    """Return accepted execution and policy identities for one derivation stage and kind.

    Fails closed: once a scoped runtime is accepted (`recipe/env`), a project,
    link or build unit whose code scope is not accepted has no code identity,
    so no code change would ever re-run it. That is a defect, not a recipe.
    Offline selection skips that kind and reports it (`schedule/fairness`).
    """
    selected = recipe_names(stage, kind)
    result = {
        str(name): str(value)
        for name, value in conn.execute(
            "SELECT input_name,digest FROM accepted_inputs WHERE consumer='pipeline' ORDER BY input_name"
        )
        if name in selected
    }
    if stage in {"project", "link", "build"} and "recipe/env" in result:
        missing = sorted(
            name
            for name in selected
            if name.startswith(recipe_scope.SCOPE_PREFIX) and name not in result
        )
        if missing:
            raise RecipeScopeMissing(f"accepted runtime has no code scope {missing[0]}")
    return result
