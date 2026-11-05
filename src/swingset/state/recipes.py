"""Capture transform declarations separately from byte and runtime audit identities."""

from __future__ import annotations

import errno
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import sysconfig
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from threading import Lock

from swingset.fetch.archive import canonical, digest
from swingset.state import engine

from . import recipe_scope

_RUNTIME_FINGERPRINTS: OrderedDict[str, str] = OrderedDict()
_RUNTIME_FINGERPRINTS_LOCK = Lock()


def environment_identity() -> dict[str, object]:
    """Identify the interpreter and installed dependency manifests.

    Distribution RECORD digests describe installed wheel contents. They are
    dependency identities, not an audit of every installed binary on each cycle.
    The project's own executed source is captured separately, byte for byte.
    """
    dependency_image = os.environ.get("SWINGSET_DEPENDENCY_IMAGE")
    if dependency_image:
        if not dependency_image.startswith("sha256:") or len(dependency_image) != 71:
            raise ValueError("invalid dependency image identity")
        return {"dependency_image": dependency_image}
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


def _runtime_files(root: Path) -> dict[str, bytes]:
    if "__pycache__" in root.parts:
        return {}
    ignored = (errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP)
    pending: list[tuple[str, tuple[str, ...]]] = [(os.fspath(root), ())]
    files: list[tuple[tuple[str, ...], str]] = []
    while pending:
        directory, relative = pending.pop()
        try:
            with os.scandir(directory) as stream:
                entries = list(stream)
        except OSError as error:
            if isinstance(error, PermissionError) or error.errno in ignored:
                continue
            raise
        for entry in entries:
            if entry.name == "__pycache__":
                continue
            parts = (*relative, entry.name)
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append((entry.path, parts))
                elif not (entry.name.endswith(".pyc") and entry.name != ".pyc") and entry.is_file():
                    files.append((parts, entry.path))
            except OSError as error:
                if error.errno not in ignored:
                    raise
    result = {}
    # Component order matches Path ordering, including prefix-named siblings.
    for parts, path in sorted(files):
        with open(path, "rb") as stream:
            result["runtime/swingset/" + "/".join(parts)] = stream.read()
    return result


def _capture_runtime_files(package_root: Path | None) -> dict[str, bytes]:
    root = package_root or Path(__file__).resolve().parents[1]
    files = _runtime_files(root)
    if not files or "runtime/swingset/__init__.py" not in files:
        raise ValueError("runtime package is missing its source artifact")
    project = root.parent.parent
    for name in ("pyproject.toml", "uv.lock"):
        path = project / name
        if path.is_file():
            files["runtime/" + name] = path.read_bytes()
    files["runtime/environment.json"] = canonical(environment_identity())
    return files


def _complete_runtime(files: dict[str, bytes]) -> dict[str, bytes]:
    environment = files.pop("runtime/environment.json")
    if "runtime/pyproject.toml" in files:
        import tomllib

        # What `recipe/env` reads of pyproject (recipe_scope.ENV_FILES).
        table = tomllib.loads(files["runtime/pyproject.toml"].decode()).get("project", {})
        files["runtime/project.json"] = canonical(table)
    files["runtime/environment.json"] = environment
    files["recipes/runtime.json"] = canonical(
        {
            "format": "swingset-runtime-recipe-v1",
            "files": {name: digest(body) for name, body in sorted(files.items())},
        }
    )
    files["recipes/scopes.json"] = canonical(recipe_scope.manifest(files))
    return files


def capture_runtime(package_root: Path | None = None) -> dict[str, bytes]:
    """Retain code, schemas, vocabularies, lock inputs, and their exact manifest."""
    return _complete_runtime(_capture_runtime_files(package_root))


def runtime_fingerprint(package_root: Path | None = None) -> str:
    """Hash the full payload, reusing derived work only for identical current bytes."""
    files = _capture_runtime_files(package_root)
    key = hashlib.sha256()
    for name, body in sorted(files.items()):
        encoded = name.encode()
        key.update(len(encoded).to_bytes(4, "big"))
        key.update(encoded)
        key.update(len(body).to_bytes(8, "big"))
        key.update(body)
    identity = key.hexdigest()
    with _RUNTIME_FINGERPRINTS_LOCK:
        cached = _RUNTIME_FINGERPRINTS.get(identity)
        if cached is not None:
            _RUNTIME_FINGERPRINTS.move_to_end(identity)
            return cached
    result = digest(
        canonical({name: digest(body) for name, body in _complete_runtime(files).items()})
    )
    with _RUNTIME_FINGERPRINTS_LOCK:
        _RUNTIME_FINGERPRINTS[identity] = result
        _RUNTIME_FINGERPRINTS.move_to_end(identity)
        while len(_RUNTIME_FINGERPRINTS) > 8:
            _RUNTIME_FINGERPRINTS.popitem(last=False)
    return result


def captured_recipe_inputs(files: Mapping[str, bytes], *, history_start: str) -> dict[str, str]:
    """Reduce captured artifacts to accepted semantic inputs, excluding scheduling.

    New scoped bundles yield declared transform versions and a byte-based
    build digest. Earlier scoped bundles reproduce their byte and environment
    identities; older bundles retain their package-wide `recipe/runtime`.
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
    member_digests: dict[str, str | None] = {}

    def member_digest(path: str) -> str | None:
        if path not in member_digests:
            member_digests[path] = recipe_scope.member_digest(files, path)
        return member_digests[path]

    for name, group in (("recipe/env", env), *members.items()):
        if name != "recipe/env" and not name.startswith(recipe_scope.SCOPE_PREFIX):
            raise ValueError(f"captured scoped recipe names a non-code input: {name}")
        if not isinstance(group, dict) or any(
            member_digest(path) != value for path, value in group.items()
        ):
            raise ValueError("captured runtime artifact differs from its scoped recipe")
        result[name] = digest(canonical(group))
    declared = scopes.get("declared")
    if declared is not None:
        if declared != recipe_scope.declared_versions(files):
            raise ValueError("captured declared versions differ from source")
        result.pop("recipe/env", None)
        for name, versions in declared.items():
            if name in members:
                result[name] = digest(canonical(versions))
    return result


def captured_audit_inputs(files: Mapping[str, bytes]) -> dict[str, str]:
    """Byte and runtime identities; audit changes queue no derivation work."""
    captured_recipe_inputs(files, history_start="")  # validate both manifests
    raw = files.get("recipes/scopes.json")
    if raw is None:
        return {}
    scopes = json.loads(raw)
    return {
        name: digest(canonical(group))
        for name, group in (("recipe/env", scopes["env"]), *scopes["scopes"].items())
    }


# `recipe/runtime` is the package-wide identity of bundles captured before
# D-0207. After the first scoped bundle it stays at the absent-input digest.
_COMMON = frozenset({"recipe/runtime", "policy/history_start"})


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


def recipe_inputs(conn: engine.Connection, stage: str, kind: str | None = None) -> dict[str, str]:
    """Return accepted execution and policy identities for one derivation stage and kind.

    Fails closed: once a scoped runtime is accepted, a project,
    link or build unit whose code scope is not accepted has no code identity,
    so no code change would ever re-run it. That is a defect, not a recipe.
    Offline selection skips that kind and reports it (`schedule/fairness`).
    """
    selected = recipe_names(stage, kind)
    result = {
        str(name): str(value)
        for name, value in conn.execute(
            "SELECT input_name, digest FROM accepted_inputs WHERE consumer = 'pipeline' ORDER BY "
            "input_name NULLS FIRST"
        )
        if name in selected
    }
    if (
        stage in {"project", "link", "build"}
        and conn.execute(
            "SELECT 1 AS _value_0 FROM accepted_inputs WHERE consumer = 'audit' OR (consumer = 'pipeline' AND input_name ILIKE 'recipe/code/%') LIMIT 1"
        ).fetchone()
        is not None
    ):
        missing = sorted(
            name
            for name in selected
            if name.startswith(recipe_scope.SCOPE_PREFIX) and name not in result
        )
        if missing:
            raise RecipeScopeMissing(f"accepted runtime has no code scope {missing[0]}")
    return result
