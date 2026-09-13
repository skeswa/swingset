"""Field dispositions shared by source-specific contracts."""

import re
from typing import Any

from .report import Field


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
