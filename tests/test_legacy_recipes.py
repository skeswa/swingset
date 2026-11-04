"""Old code-scope witnesses are structural, streaming and fully verified."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swingset.fetch.archive import canonical, digest
from swingset.state.legacy_recipes import (
    MissingLegacyRecipes,
    resolve_scope_bundles,
    scope_references,
    verify_bundle,
)

SCOPE = "recipe/code/parse/example.page"
CODE = "a" * 64
ENV = "b" * 64


def pieces(value: bytes, width=1):
    yield from (value[index : index + width] for index in range(0, len(value), width))


@pytest.mark.parametrize(
    "value",
    [
        {"accepted_inputs": [["pipeline", "recipe/env", ENV], ["pipeline", SCOPE, CODE]]},
        {"inputs": {SCOPE: CODE, "recipe/env": ENV}},
    ],
)
def test_both_legacy_shapes_and_escaped_keys(value):
    encoded = (
        json.dumps(value)
        .encode()
        .replace(b"recipe/env", b"recipe\\u002fenv")
        .replace(b"recipe/code", b"recipe\\/co\\u0064e")
    )
    assert scope_references(pieces(encoded)) == {(SCOPE, CODE)}


def test_environment_marker_is_only_a_semantic_pipeline_input():
    assert not scope_references(
        pieces(canonical({"context": {"recipe/env": ENV}, "inputs": {SCOPE: CODE}}))
    )
    assert not scope_references(
        pieces(
            canonical(
                {"accepted_inputs": [["audit", "recipe/env", ENV], ["pipeline", SCOPE, CODE]]}
            )
        )
    )
    assert not scope_references(
        pieces(
            canonical(
                {
                    "inputs": {SCOPE: CODE},
                    "other": {"accepted_inputs": [["pipeline", "recipe/env", ENV]]},
                }
            )
        )
    )
    assert not scope_references(
        pieces(
            canonical(
                {"inputs": {SCOPE: CODE}, "accepted_inputs": [["pipeline", "recipe/env", ENV]]}
            )
        )
    )


def test_large_irrelevant_string_is_streamed():
    def stream():
        yield b'{"padding":"'
        for _ in range(512):
            yield b"x" * 65536
        yield b'","context":{"name":"caf\xc3'
        yield b'\xa9"},"inputs":' + canonical({SCOPE: CODE, "recipe/env": ENV}) + b"}"

    assert scope_references(stream()) == {(SCOPE, CODE)}


@pytest.mark.parametrize(
    "body",
    [
        b'{"inputs":{"recipe/env":',
        b'{"inputs":{},}',
        b'{"inputs":{}}{}',
        b'{"inputs":{"recipe/env":null}}',
        b'{"accepted_inputs":[["pipeline","recipe/env"]]}',
        b'{"inputs":{"recipe/env":"' + ENV.encode() + b'","recipe/code/parse/example.page":{}}}',
        b'{"inputs":"recipe/env"}',
        b'{"context":"bad\\q"}',
    ],
)
def test_invalid_recipes_refuse(body):
    with pytest.raises(ValueError):
        scope_references(pieces(body, 7))


def bundle(tmp: Path, *, policy=b"accepted"):
    code = b"VERSION = 1\n"
    members = {"runtime/swingset/example.py": code, "overrides/policy.csv": policy}
    group = {"runtime/swingset/example.py": digest(code)}
    members["recipes/scopes.json"] = canonical(
        {"format": "swingset-scoped-recipe-v1", "env": {}, "scopes": {SCOPE: group}}
    )
    manifest = {name: digest(body) for name, body in members.items()}
    identity = digest(canonical(manifest))
    root = tmp / "inputs" / identity
    for name, body in {**members, "manifest.json": canonical(manifest)}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    files = {path.relative_to(tmp).as_posix(): path for path in tmp.rglob("*") if path.is_file()}
    return identity, group, files


def test_prefers_explicit_accepted_witness_and_verifies_all_members(tmp_path):
    first, group, files = bundle(tmp_path)
    other, _, files = bundle(tmp_path, policy=b"unaccepted proposal")
    ref = (SCOPE, digest(canonical(group)))
    assert resolve_scope_bundles(files, {ref}, preferred={first}) == {ref: first}
    verified = verify_bundle(files, first, groups={SCOPE: group})
    assert "inputs/" + first + "/overrides/policy.csv" in verified
    files["inputs/" + first + "/overrides/policy.csv"].write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        verify_bundle(files, first, groups={SCOPE: group})
    assert resolve_scope_bundles(files, {ref}, preferred={first}) == {ref: other}
    files["inputs/" + other + "/runtime/swingset/example.py"].unlink()
    with pytest.raises(MissingLegacyRecipes) as error:
        resolve_scope_bundles(files, {ref})
    assert error.value.missing == {ref}


def test_dispatch_code_identity_uses_the_existing_static_constructor(tmp_path):
    from swingset.state.recipe_scope import dispatch_code

    module = b'ADAPTERS = {"example":"module"}\nKIND_OWNERS = {"page":"example"}\ndef load():\n    return ADAPTERS\n'
    group = {"runtime/swingset/sources/registry.py#code": dispatch_code(module)}
    members = {
        "runtime/swingset/sources/registry.py": module,
        "recipes/scopes.json": canonical(
            {"format": "swingset-scoped-recipe-v1", "env": {}, "scopes": {SCOPE: group}}
        ),
    }
    manifest = {name: digest(value) for name, value in members.items()}
    identity = digest(canonical(manifest))
    root = tmp_path / "inputs" / identity
    for name, value in {**members, "manifest.json": canonical(manifest)}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    files = {
        path.relative_to(tmp_path).as_posix(): path
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    ref = (SCOPE, digest(canonical(group)))
    assert resolve_scope_bundles(files, {ref}) == {ref: identity}
