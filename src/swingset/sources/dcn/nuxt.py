"""Decode the reviewed Nuxt data serialization without executing JavaScript."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from selectolax.parser import HTMLParser

from swingset.sources.base import ExtractError

MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_SCRIPT_BYTES = 1024 * 1024
MAX_NODES = 50_000
MAX_DEPTH = 64
IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
PREFIX = re.compile(r"\s*window\.__NUXT__\s*=\s*\(function\s*\(([^()]*)\)\s*\{\s*return\s+")
UNDEFINED = object()


@dataclass(frozen=True)
class Reference:
    name: str


class LiteralReader:
    def __init__(self, source: str, position: int = 0) -> None:
        self.source, self.position, self.nodes = source, position, 0

    def whitespace(self) -> None:
        while self.position < len(self.source) and self.source[self.position].isspace():
            self.position += 1

    def take(self, token: str) -> bool:
        self.whitespace()
        if self.source.startswith(token, self.position):
            self.position += len(token)
            return True
        return False

    def require(self, token: str) -> None:
        if not self.take(token):
            raise ExtractError("DCN payload has unsupported grammar")

    def word(self) -> str:
        self.whitespace()
        match = IDENTIFIER.match(self.source, self.position)
        if match is None:
            raise ExtractError("DCN payload expected a literal property or parameter")
        self.position = match.end()
        return match[0]

    def value(self, *, references: bool, depth: int = 0) -> Any:
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise ExtractError("DCN payload exceeds structural bounds")
        self.whitespace()
        if self.position >= len(self.source):
            raise ExtractError("DCN payload is truncated")
        if self.take("{"):
            result = {}
            if not self.take("}"):
                while True:
                    self.whitespace()
                    key = (
                        self.value(references=False, depth=depth + 1)
                        if self.source.startswith('"', self.position)
                        else self.word()
                    )
                    if not isinstance(key, str) or key in result:
                        raise ExtractError("DCN payload has duplicate or invalid object keys")
                    self.require(":")
                    result[key] = self.value(references=references, depth=depth + 1)
                    if self.take("}"):
                        break
                    self.require(",")
            return result
        if self.take("["):
            items = []
            if not self.take("]"):
                while True:
                    items.append(self.value(references=references, depth=depth + 1))
                    if self.take("]"):
                        break
                    self.require(",")
            return items
        if self.source.startswith('"', self.position):
            try:
                value, end = json.JSONDecoder().raw_decode(self.source, self.position)
            except ValueError as exc:
                raise ExtractError("DCN payload has an invalid string") from exc
            self.position = end
            return value
        match = NUMBER.match(self.source, self.position)
        if match:
            try:
                number = json.loads(match[0])
            except ValueError as exc:
                raise ExtractError("DCN payload has an invalid number") from exc
            if isinstance(number, float) and not math.isfinite(number):
                raise ExtractError("DCN payload has a non-finite number")
            self.position = match.end()
            return number
        word = self.word()
        if word in {"true", "false", "null"}:
            return {"true": True, "false": False, "null": None}[word]
        if word == "void":
            self.require("0")
            return UNDEFINED
        if references:
            return Reference(word)
        raise ExtractError("DCN payload argument is not a data literal")


def decode_script(source: str) -> dict[str, Any]:
    """Accept only the retained IIFE's data-literal and parameter-reference grammar."""
    if len(source.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise ExtractError("DCN payload exceeds script byte bound")
    match = PREFIX.match(source)
    if match is None:
        raise ExtractError("DCN payload is not the reviewed data serialization")
    parameters = [name.strip() for name in match[1].split(",")] if match[1].strip() else []
    if (
        len(parameters) > 1024
        or len(set(parameters)) != len(parameters)
        or any(
            not IDENTIFIER.fullmatch(name)
            or name in {"true", "false", "null", "void", "return", "function", "new", "this"}
            for name in parameters
        )
    ):
        raise ExtractError("DCN payload has invalid parameters")
    reader = LiteralReader(source, match.end())
    raw = reader.value(references=True)
    reader.take(";")
    reader.require("}")
    reader.require("(")
    arguments = []
    if not reader.take(")"):
        while True:
            arguments.append(reader.value(references=False))
            if reader.take(")"):
                break
            reader.require(",")
    reader.require(")")
    reader.take(";")
    reader.whitespace()
    if reader.position != len(source) or len(parameters) != len(arguments):
        raise ExtractError("DCN payload has trailing code or mismatched arguments")
    bindings = dict(zip(parameters, arguments, strict=True))
    resolved_nodes = 0

    def resolve(value: Any, depth: int = 0) -> Any:
        nonlocal resolved_nodes
        resolved_nodes += 1
        if resolved_nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ExtractError("DCN payload expansion exceeds structural bounds")
        if isinstance(value, Reference):
            if value.name not in bindings:
                raise ExtractError("DCN payload has an unknown parameter")
            return resolve(bindings[value.name], depth + 1)
        if isinstance(value, dict):
            output = {}
            for key, item in value.items():
                resolved = resolve(item, depth + 1)
                if resolved is not UNDEFINED:
                    output[key] = resolved
            return output
        if isinstance(value, list):
            return [
                None if (resolved := resolve(item, depth + 1)) is UNDEFINED else resolved
                for item in value
            ]
        return value

    result = resolve(raw)
    if not isinstance(result, dict):
        raise ExtractError("DCN payload root is not an object")
    return result


def evaluate_nuxt(body: bytes) -> dict[str, Any]:
    """Pure compatibility boundary: decode data only, without Node, eval or I/O."""
    if len(body) > MAX_BODY_BYTES:
        raise ExtractError("DCN HTML exceeds body byte bound")
    try:
        tree = HTMLParser(body.decode("utf-8"))
    except UnicodeError as exc:
        raise ExtractError("DCN HTML is not valid UTF-8") from exc
    scripts = [node.text() for node in tree.css("script") if "window.__NUXT__" in node.text()]
    if len(scripts) != 1:
        raise ExtractError("DCN page requires exactly one Nuxt data payload")
    return decode_script(scripts[0])
