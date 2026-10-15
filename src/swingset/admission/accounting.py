"""Field dispositions and the accounting result shared by every kind's contract."""

import re
from dataclasses import dataclass
from typing import Any

from .report import Field, Guard


@dataclass(frozen=True)
class Accounting:
    """What a kind's contract module counted; `contracts.inspect` adds the rest.

    `removal` is the authority the kind proposes. A module with `own_warnings`
    accounts for the parse warnings itself; otherwise each warning becomes a
    field, excluded when its code is in `bounded_warnings` (D-0257).
    """

    fields: tuple[Field, ...]
    guards: tuple[Guard, ...]
    source_count: int | None
    interpreted_count: int
    listed_children: tuple[str, ...] = ()
    interpreted_children: tuple[str, ...] = ()
    terminal: str | None = "captured_document_end"
    removal: str = "none"
    bounded_warnings: frozenset[str] = frozenset()
    own_warnings: bool = False


def account_keys(value: Any, known: set[str], path: str, fields: list[Field]) -> None:
    if not isinstance(value, dict):
        fields.append(Field(path, "unknown", "Expected an object"))
        return
    for key in sorted(value):
        field_path = re.sub(r"\[\d+\]|^\d+", "[*]", path) if key in known else path
        fields.append(
            Field(
                f"{field_path}.{key}",
                "handled" if key in known else "unknown",
                "Declared source field" if key in known else "Undeclared source field",
            )
        )
