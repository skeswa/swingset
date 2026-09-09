"""Small pure parsing helpers."""

from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import parse_qs, urljoin, urlparse

from selectolax.parser import HTMLParser

from .base import ExtractError, JsonValue


def text(value: str) -> str:
    return " ".join(html.unescape(value).split())


def source_key(prefix: str, url: str) -> str:
    return f"{prefix}:{hashlib.sha256(url.encode()).hexdigest()[:16]}"


def query_value(url: str, key: str) -> str | None:
    values = parse_qs(urlparse(url).query).get(key)
    return values[0] if values else None


def absolute(base: str, href: str) -> str:
    return urljoin(base, html.unescape(href))


def as_dict(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ExtractError("expected a JSON object")
    return value


def tags(source: str, tag: str) -> list[tuple[str, str, str]]:
    """Return attributes, markup, and text from selectolax nodes."""
    result: list[tuple[str, str, str]] = []
    wrapped = (
        f"<table>{source}</table>"
        if tag == "tr"
        else f"<table><tr>{source}</tr></table>"
        if tag in {"td", "th"}
        else source
    )
    for node in HTMLParser(wrapped).css(tag):
        attributes = " ".join(
            f'{key}="{html.escape(value or "", quote=True)}"'
            for key, value in sorted(node.attributes.items())
        )
        result.append((attributes, node.html or "", text(node.text(separator=" "))))
    return result


def attr(attrs: str, name: str) -> str | None:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*(['\"])(.*?)\1", attrs, re.I | re.S)
    return html.unescape(match.group(2)) if match else None


def canonical_attrs(attrs: str, names: tuple[str, ...]) -> dict[str, JsonValue]:
    return {name: value for name in names if (value := attr(attrs, name)) is not None}
