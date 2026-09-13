"""Stable, lossy name normalization used only for matching."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

SUFFIXES = frozenset({"jr", "sr", "ii", "iii"})


def paired_names(raw: str) -> tuple[str, ...]:
    """Recognize explicit person separators before lossy normalization.

    This identifies ownership uncertainty; it does not assign dancing roles.
    Hyphens, apostrophes, particles, and multiple given names are not separators.
    """
    parts = tuple(
        part.strip() for part in re.split(r"\s+and\s+|\s*[&/]\s*", raw, flags=re.IGNORECASE)
    )
    return parts if len(parts) > 1 and all(parts) else ()


@dataclass(frozen=True)
class NormalizedName:
    value: str
    tokens: tuple[str, ...]
    first_token: str
    last_token: str
    suffix: str | None


@lru_cache(maxsize=65_536)
def normalize_name(raw: str) -> NormalizedName:
    normalized = unicodedata.normalize("NFKC", raw)
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFD", normalized)
        if unicodedata.category(char) != "Mn"
    )
    normalized = "".join(char if char.isalnum() else " " for char in normalized.casefold())
    tokens = re.sub(r"\s+", " ", normalized).strip().split()
    suffix = tokens.pop() if tokens and tokens[-1] in SUFFIXES else None
    value = " ".join(tokens)
    return NormalizedName(
        value, tuple(tokens), tokens[0] if tokens else "", tokens[-1] if tokens else "", suffix
    )


def nickname_equivalent(
    left: NormalizedName, right: NormalizedName, nicknames: dict[str, str]
) -> bool:
    if left.last_token != right.last_token or not left.first_token or not right.first_token:
        return False
    canonical_left = nicknames.get(left.first_token, left.first_token)
    canonical_right = nicknames.get(right.first_token, right.first_token)
    return canonical_left == canonical_right
