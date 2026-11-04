"""Observe SQL text without interpolating values into test logs."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any


def observe(conn: Any, callback: Callable[[str], None]) -> AbstractContextManager[None]:
    return conn.observing(
        lambda statement, _params: callback(
            statement.as_string(conn) if hasattr(statement, "as_string") else str(statement)
        )
    )
