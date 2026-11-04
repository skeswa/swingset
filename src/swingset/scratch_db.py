"""Disposable DuckDB storage with bounded insert batches and spillable key scans."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from itertools import islice
from pathlib import Path
from typing import Any

import duckdb

BATCH_ROWS = 128


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(
        str(path),
        config={
            "threads": "1",
            "memory_limit": "32MB",
            "preserve_insertion_order": "false",
            "temp_directory": str(path) + ".tmp",
        },
    )


def insert_many(
    conn: duckdb.DuckDBPyConnection, statement: str, values: Iterable[tuple[Any, ...]]
) -> None:
    """Commit bounded batches; scratch data has no cross-batch durability promise."""
    source = iter(values)
    while batch := list(islice(source, BATCH_ROWS)):
        conn.execute("BEGIN")
        try:
            conn.executemany(statement, batch)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def scan_keys(
    conn: duckdb.DuckDBPyConnection, statement: str, params: tuple[Any, ...] = ()
) -> Iterator[tuple[Any, ...]]:
    """Stream committed keys while the writer performs lookups or mutations.

    Callers select only keys here: sorting large payloads can require memory
    that DuckDB cannot spill. The independent cursor sees committed scratch data.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(statement, params)
        while batch := cursor.fetchmany(BATCH_ROWS):
            yield from batch
    finally:
        cursor.close()
