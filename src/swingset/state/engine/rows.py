"""Rows preserve SQLite's Python-level value and lookup contract."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, overload


class Row(Sequence[Any]):
    """Values iterate in column order; names match without ASCII case."""

    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._lookup = {name.lower(): index for index, name in enumerate(columns)}
        if len(self._lookup) != len(self._columns):
            raise ValueError("database result has duplicate column labels")
        if len(self._columns) != len(self._values):
            raise ValueError("database result columns and values differ")

    @overload
    def __getitem__(self, key: int) -> Any: ...

    @overload
    def __getitem__(self, key: slice) -> tuple[Any, ...]: ...

    @overload
    def __getitem__(self, key: str) -> Any: ...

    def __getitem__(self, key: int | slice | str) -> Any:
        if isinstance(key, str):
            try:
                key = self._lookup[key.lower()]
            except KeyError:
                raise IndexError("No item with that key") from None
        return self._values[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> list[str]:
        return list(self._columns)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Row)
            and self._columns == other._columns
            and self._values == other._values
        )

    def __hash__(self) -> int:
        return hash((self._columns, self._values))


def row_factory(cursor: Any) -> Any:
    columns = tuple(column.name for column in cursor.description or ())
    if len({name.lower() for name in columns}) != len(columns):
        raise ValueError("database result has duplicate column labels")
    return lambda values: Row(columns, values)
