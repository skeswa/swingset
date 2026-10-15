"""Mark the adapters whose parse results carry an accounting declaration.

`declared` only marks a page kind's `parse`. Admission attaches the
declaration where it parses a page (`admission.contracts.declare`), so no
adapter imports admission, and an edit to admission rules changes no
extract scope and no project or link scope (D-0261).
"""

from collections.abc import Callable
from typing import Any

from .base import ParseContext, ParseResult


def declared[T](
    parse: Callable[[T, Any, ParseContext], ParseResult],
) -> Callable[[T, Any, ParseContext], ParseResult]:
    parse.declares = True  # type: ignore[attr-defined]
    return parse


def declares(page: object) -> bool:
    """Whether a page kind's parse is `declared`."""
    return bool(getattr(getattr(page, "parse", None), "declares", False))
