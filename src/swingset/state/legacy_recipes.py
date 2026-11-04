"""Resolve old scoped byte identities to fully verified retained code bundles.

Only semantic recipe input fields create these references. The streaming reader
never materializes unrelated strings, and archive lookup never executes code.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import re
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from swingset.fetch.archive import canonical

ScopeReference = tuple[str, str]
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ARCHIVE = re.compile(r"inputs/([0-9a-f]{64})/recipes/scopes\.json\Z")
_SPECIAL = re.compile(r'["\\\x00-\x1f]')
_BARE_END = re.compile(r'[ \t\r\n{}\[\],:"]')
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


def _lex(chunks: Iterable[bytes]) -> Iterator[tuple[str, str | None]]:
    """JSON tokens, retaining at most 1 KiB of any string or primitive."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    quoted = False
    escape = False
    unicode_left = 0
    raw: list[str] = []
    size = 0
    oversized = False
    bare = ""

    def append(value: str) -> None:
        nonlocal size, oversized
        size += len(value)
        if size > 1024:
            oversized = True
            raw.clear()
        elif not oversized:
            raw.append(value)

    def primitive(value: str) -> tuple[str, None]:
        if value not in {"true", "false", "null"} and _NUMBER.fullmatch(value) is None:
            raise ValueError("invalid JSON primitive in a recipe")
        return "scalar", None

    for block in chunks:
        text = decoder.decode(block)
        position = 0
        while position < len(text):
            if quoted:
                if unicode_left:
                    char = text[position]
                    if char not in "0123456789abcdefABCDEF":
                        raise ValueError("invalid JSON unicode escape in a recipe")
                    append(char)
                    unicode_left -= 1
                    position += 1
                    continue
                if escape:
                    char = text[position]
                    if char not in '"\\/bfnrtu':
                        raise ValueError("invalid JSON escape in a recipe")
                    append(char)
                    escape = False
                    position += 1
                    if char == "u":
                        unicode_left = 4
                    continue
                found = _SPECIAL.search(text, position)
                end = len(text) if found is None else found.start()
                append(text[position:end])
                position = end
                if found is None:
                    continue
                char = text[position]
                position += 1
                if char == '"':
                    yield "string", None if oversized else json.loads('"' + "".join(raw) + '"')
                    quoted = False
                    raw = []
                    size = 0
                    oversized = False
                elif char == "\\":
                    append(char)
                    escape = True
                else:
                    raise ValueError("unescaped JSON control character in a recipe")
                continue
            if bare:
                found = _BARE_END.search(text, position)
                end = len(text) if found is None else found.start()
                bare += text[position:end]
                position = end
                if len(bare) > 1024:
                    raise ValueError("oversized JSON primitive in a recipe")
                if found is None:
                    continue
                yield primitive(bare)
                bare = ""
                continue
            char = text[position]
            if char in " \t\r\n":
                position += 1
            elif char == '"':
                quoted = True
                position += 1
            elif char in "{}[]:,":
                yield char, None
                position += 1
            else:
                bare = char
                position += 1
    decoder.decode(b"", final=True)
    if quoted or escape or unicode_left:
        raise ValueError("unterminated JSON string in a recipe")
    if bare:
        yield primitive(bare)


@dataclass
class _Frame:
    kind: str
    path: tuple[str | int | None, ...]
    state: str
    key: str | None = None
    index: int = 0
    entry: list[str | None] | None = None


def scope_references(chunks: Iterable[bytes]) -> frozenset[ScopeReference]:
    """Read legacy $.inputs maps and $.accepted_inputs pipeline triples only."""
    stack: list[_Frame] = []
    started = False
    finished = False
    legacy = {"inputs": False, "accepted_inputs": False}
    pairs: dict[str, set[ScopeReference]] = {"inputs": set(), "accepted_inputs": set()}

    def semantic(field: str, name: str | None, value: str | None) -> None:
        if name is None:
            raise ValueError("oversized semantic input name in a recipe")
        if name == "recipe/env":
            if value is None or _HASH.fullmatch(value) is None:
                raise ValueError("invalid legacy environment identity in a recipe")
            legacy[field] = True
        elif name.startswith("recipe/code/"):
            if value is None or _HASH.fullmatch(value) is None:
                raise ValueError("invalid scoped code identity in a recipe")
            pairs[field].add((name, value))

    def scalar(path: tuple[str | int | None, ...], kind: str, value: str | None) -> None:
        if path in {("inputs",), ("accepted_inputs",)}:
            raise ValueError("semantic recipe input fields must be containers")
        if len(path) == 2 and path[0] == "inputs":
            semantic(
                "inputs",
                path[1] if isinstance(path[1], str) else None,
                value if kind == "string" else None,
            )
        if (
            len(path) == 3
            and path[0] == "accepted_inputs"
            and stack
            and stack[-1].entry is not None
        ):
            stack[-1].entry.append(value if kind == "string" else None)

    for kind, value in _lex(chunks):
        if not stack:
            if finished:
                raise ValueError("multiple JSON values in a recipe")
            if started:
                raise ValueError("invalid JSON recipe root")
            started = True
            if kind != "{":
                raise ValueError("recipe root must be an object")
            stack.append(_Frame("{", (), "key_or_end"))
            continue
        frame = stack[-1]
        if kind in {"}", "]"}:
            valid = frame.state in (
                {"key_or_end", "comma_or_end"}
                if frame.kind == "{"
                else {"value_or_end", "comma_or_end"}
            )
            if kind != ("}" if frame.kind == "{" else "]") or not valid:
                raise ValueError("invalid JSON container ending in a recipe")
            if frame.entry is not None:
                if len(frame.entry) != 3 or any(item is None for item in frame.entry):
                    raise ValueError("recipe accepted input must contain three strings")
                if frame.entry[0] == "pipeline":
                    semantic("accepted_inputs", frame.entry[1], frame.entry[2])
            stack.pop()
            if not stack:
                finished = True
            continue
        if frame.kind == "{" and frame.state in {"key_or_end", "key"}:
            if kind != "string":
                raise ValueError("recipe object key must be a string")
            frame.key = value
            frame.state = "colon"
            continue
        if frame.state == "colon":
            if kind != ":":
                raise ValueError("recipe object key has no colon")
            frame.state = "value"
            continue
        if frame.state == "comma_or_end":
            if kind != ",":
                raise ValueError("recipe values have no comma")
            frame.state = "key" if frame.kind == "{" else "value"
            continue
        if frame.state not in {"value", "value_or_end"}:
            raise ValueError("invalid JSON recipe value")
        path = (*frame.path, frame.key if frame.kind == "{" else frame.index)
        frame.state = "comma_or_end"
        if frame.kind == "[":
            frame.index += 1
        if kind in {"{", "["}:
            if (
                len(path) == 2
                and path[0] == "inputs"
                and (
                    path[1] == "recipe/env"
                    or isinstance(path[1], str)
                    and path[1].startswith("recipe/code/")
                )
            ):
                raise ValueError("semantic code identities must be strings")
            if path == ("inputs",) and kind != "{":
                raise ValueError("recipe inputs must be an object")
            if path == ("accepted_inputs",) and kind != "[":
                raise ValueError("recipe accepted_inputs must be an array")
            entry: list[str | None] | None = (
                [] if len(path) == 2 and path[0] == "accepted_inputs" else None
            )
            if entry is not None and kind != "[":
                raise ValueError("recipe accepted input must be an array")
            stack.append(
                _Frame(kind, path, "key_or_end" if kind == "{" else "value_or_end", entry=entry)
            )
        elif kind in {"string", "scalar"}:
            scalar(path, kind, value)
        else:
            raise ValueError("invalid JSON recipe value")
    if not finished or stack:
        raise ValueError("incomplete JSON recipe")
    return frozenset(pair for field, values in pairs.items() if legacy[field] for pair in values)


class MissingLegacyRecipes(ValueError):
    def __init__(self, missing: Collection[ScopeReference]) -> None:
        self.missing = frozenset(missing)
        super().__init__(f"{len(self.missing)} legacy code scopes have no verified retained bundle")


def _hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("legacy bundle member is not a safe relative path")
    return path


def verify_bundle(
    files: Mapping[str, Path], bundle: str, *, groups: Mapping[str, Any] | None = None
) -> frozenset[str]:
    """Verify the complete immutable archive, including dispatch-code members."""
    from .recipe_scope import member_digest

    if _HASH.fullmatch(bundle) is None:
        raise ValueError("invalid legacy bundle identity")
    prefix = "inputs/" + bundle + "/"
    manifest_path = files.get(prefix + "manifest.json")
    if manifest_path is None:
        raise ValueError("legacy bundle manifest is missing")
    manifest = json.loads(manifest_path.read_bytes())
    if not isinstance(manifest, dict) or hashlib.sha256(canonical(manifest)).hexdigest() != bundle:
        raise ValueError("legacy bundle manifest identity differs")
    names = {prefix + "manifest.json"}
    for name, expected in manifest.items():
        if (
            not isinstance(name, str)
            or not isinstance(expected, str)
            or _HASH.fullmatch(expected) is None
        ):
            raise ValueError("legacy bundle manifest member is invalid")
        _member(name)
        path = files.get(prefix + name)
        if path is None or not path.is_file() or _hash_file(path) != expected:
            raise ValueError("legacy bundle member is missing or corrupt")
        names.add(prefix + name)
    scopes_path = files.get(prefix + "recipes/scopes.json")
    if scopes_path is None or "recipes/scopes.json" not in manifest:
        raise ValueError("legacy bundle scope manifest is missing")
    scopes = json.loads(scopes_path.read_bytes())
    if (
        not isinstance(scopes, dict)
        or scopes.get("format") != "swingset-scoped-recipe-v1"
        or not isinstance(scopes.get("env"), dict)
        or not isinstance(scopes.get("scopes"), dict)
    ):
        raise ValueError("legacy bundle scope manifest is invalid")
    actual_members: dict[str, str | None] = {}
    for group in (scopes["env"], *scopes["scopes"].values(), *(groups or {}).values()):
        if not isinstance(group, dict):
            raise ValueError("legacy scope member map is invalid")
        for member, expected in group.items():
            if not isinstance(member, str) or not isinstance(expected, str):
                raise ValueError("legacy scope member is invalid")
            name, _, suffix = member.partition("#")
            _member(name)
            if name not in manifest:
                raise ValueError("legacy scope member is absent from its bundle")
            if member not in actual_members:
                try:
                    actual_members[member] = (
                        member_digest({name: files[prefix + name].read_bytes()}, member)
                        if suffix
                        else manifest[name]
                    )
                except (SyntaxError, UnicodeError):
                    raise ValueError("legacy bundle dispatch module is invalid") from None
            if actual_members[member] != expected:
                raise ValueError("legacy scope member identity differs")
    return frozenset(names)


def resolve_scope_bundles(
    files: Mapping[str, Path],
    references: Collection[ScopeReference],
    *,
    preferred: Collection[str] = (),
) -> dict[ScopeReference, str]:
    """Choose a deterministic fully verified witness, preferring explicit roots."""
    remaining = set(references)
    result: dict[ScopeReference, str] = {}
    selected = set(preferred)
    candidates = []
    for name, path in files.items():
        if match := _ARCHIVE.fullmatch(name):
            candidates.append((match.group(1), path))
    for bundle, path in sorted(candidates, key=lambda item: (item[0] not in selected, item[0])):
        if not remaining:
            break
        try:
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict) or value.get("format") != "swingset-scoped-recipe-v1":
                continue
            groups = value.get("scopes")
            if not isinstance(groups, dict):
                continue
            matched = {
                ref
                for ref in remaining
                if ref[0] in groups
                and hashlib.sha256(canonical(groups[ref[0]])).hexdigest() == ref[1]
            }
            if not matched:
                continue
            verify_bundle(files, bundle, groups={scope: groups[scope] for scope, _ in matched})
        except (OSError, TypeError, ValueError, KeyError):
            continue
        for ref in matched:
            result[ref] = bundle
        remaining.difference_update(matched)
    if remaining:
        raise MissingLegacyRecipes(remaining)
    return result
