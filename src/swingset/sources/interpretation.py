"""Attach pure accounting to adapter results without accessing state or bytes."""

from collections.abc import Callable
from dataclasses import replace
from functools import wraps
from typing import Any

from .base import ParseContext, ParseResult


def declared[T](
    parse: Callable[[T, Any, ParseContext], ParseResult],
) -> Callable[[T, Any, ParseContext], ParseResult]:
    @wraps(parse)
    def wrapped(self: T, extract: Any, ctx: ParseContext) -> ParseResult:
        from swingset.admission.contracts import inspect

        result = parse(self, extract, ctx)
        # The evidence layer separately validates raw pagination and hashes;
        # this declaration only interprets the adapter's extracted structure.
        return replace(result, interpretation=inspect(ctx, b"", extract, result))

    return wrapped
