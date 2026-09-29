"""Compare public facts and sort generated changes with bounded working memory."""

from __future__ import annotations

import json
import pickle
import sqlite3
from collections.abc import Generator, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

Row = Mapping[str, Any]


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    try:
        # These files are disposable build intermediates, never recovery authority.
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=FILE")
        conn.execute("PRAGMA cache_size=-8192")
        return conn
    except BaseException:
        conn.close()
        path.unlink(missing_ok=True)
        raise


def sort_key(row: Row, fields: tuple[str, ...]) -> str:
    return json.dumps(tuple(row.get(field) for field in fields), default=str)


def _key(table: str, row: Row, fields: tuple[str, ...], *, baseline: bool) -> tuple[Any, ...]:
    if baseline and table == "coverage" and "scope_kind" not in row and "scope_id" not in row:
        row = {**row, "scope_kind": "year", "scope_id": str(row["year"])}
    return tuple(row.get(field) for field in fields)


def _baseline_rows(baseline: Path | None, table: str) -> Iterator[Row]:
    if baseline is not None:
        for path in sorted((baseline / "data" / table).glob("*.parquet")):
            for batch in pq.ParquetFile(path).iter_batches(batch_size=1024):
                yield from batch.to_pylist()


def _encoded_rows(
    table: str, source: Iterable[Row], fields: tuple[str, ...], *, baseline: bool
) -> Iterator[tuple[Any, ...]]:
    for row in source:
        key = _key(table, row, fields, baseline=baseline)
        yield repr(key), pickle.dumps(key), pickle.dumps(dict(row))


def changes(
    current: Mapping[str, Sequence[Row]],
    baseline: Path | None,
    keys: Mapping[str, tuple[str, ...]],
    *,
    changed_at: datetime,
    run_id: str,
    scratch: Path,
) -> Generator[dict[str, Any], None, None]:
    """Yield the original table/key/field order, holding at most one row pair.

    Pickle is only an internal encoding of rows read in this invocation. It
    preserves dates and scalar types for the original Python field comparison.
    Public primary keys have fixed schema types; repr preserves their historical
    comparison order and distinguishes nullable and textual key values.
    """
    conn = _open(scratch)
    try:
        conn.execute(
            "CREATE TABLE facts (key TEXT PRIMARY KEY, row_key BLOB, before BLOB, after BLOB)"
        )
        for table, new_rows in current.items():
            if table == "changelog" or table not in keys:
                continue
            conn.execute("DELETE FROM facts")
            for side, source in (("before", _baseline_rows(baseline, table)), ("after", new_rows)):
                conn.executemany(
                    f"INSERT INTO facts(key,row_key,{side}) VALUES (?,?,?) "
                    f"ON CONFLICT(key) DO UPDATE SET row_key=excluded.row_key,{side}=excluded.{side}",
                    _encoded_rows(table, source, keys[table], baseline=side == "before"),
                )
            conn.commit()
            for key_blob, old_blob, new_blob in conn.execute(
                "SELECT row_key,before,after FROM facts ORDER BY key"
            ):
                yield from change_rows(
                    table,
                    pickle.loads(key_blob),
                    pickle.loads(old_blob) if old_blob is not None else None,
                    pickle.loads(new_blob) if new_blob is not None else None,
                    changed_at=changed_at,
                    run_id=run_id,
                )
    finally:
        conn.close()
        scratch.unlink(missing_ok=True)


def change_rows(
    table: str,
    row_key: tuple[Any, ...],
    old: Row | None,
    new: Row | None,
    *,
    changed_at: datetime,
    run_id: str,
) -> Iterator[dict[str, Any]]:
    """The change rows of one key: added, removed, or one per differing field."""
    fields: Sequence[str | None] = (
        [None]
        if old is None or new is None
        else sorted(field for field in old.keys() | new.keys() if old.get(field) != new.get(field))
    )
    for field in fields:
        old_value = old if field is None else old.get(field) if old else None
        new_value = new if field is None else new.get(field) if new else None
        reason = (
            "suppression"
            if new and new.get("link_status") == "suppressed"
            else "link_downgraded"
            if table in {"entries", "judges", "identity_links", "placements"}
            and field is not None
            and ("wsdc_id" in field or field.startswith("registry_points_"))
            and old_value is not None
            and new_value is None
            else "new_source_data"
        )
        yield {
            "changed_at": changed_at,
            "run_id": run_id,
            "table": table,
            "record_key": json.dumps(row_key, default=str),
            "field": field,
            "old_value": json.dumps(old_value, sort_keys=True, default=str),
            "new_value": json.dumps(new_value, sort_keys=True, default=str),
            "change_type": "added" if old is None else "removed" if new is None else "updated",
            "reason": reason,
        }


_SIDE = "__side"


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _files(paths: Sequence[Path]) -> str:
    return "[" + ", ".join("'" + str(path).replace("'", "''") + "'" for path in paths) + "]"


def joined_changes(
    db: Any,
    table: str,
    current: Sequence[Path],
    baseline: Path | None,
    fields: tuple[str, ...],
    *,
    changed_at: datetime,
    run_id: str,
    batch_rows: int = 2048,
    meter: Any = None,
) -> Iterator[dict[str, Any]]:
    """`changes` for one table, streamed: a DuckDB full outer join on the key.

    ``current`` are the table's final Parquet files and ``db`` a DuckDB
    connection whose time zone is UTC. Each key's pair of rows is compared
    in Python by `change_rows`, one batch at a time, so the rows emitted
    equal `changes`' rows for the same values; only their order differs,
    and the changelog is sorted afterwards.
    """
    previous = sorted((baseline / "data" / table).glob("*.parquet")) if baseline else []
    sides = []
    for alias, paths, is_baseline in (("n", current, False), ("b", previous, True)):
        if paths:
            columns = set(pq.read_schema(paths[0]).names)
            keys = [
                ("'year'" if field == "scope_kind" else 'CAST("year" AS VARCHAR)')
                if is_baseline
                and table == "coverage"
                and field in {"scope_kind", "scope_id"}
                and not {"scope_kind", "scope_id"} & columns
                else _quoted(field)
                for field in fields
            ]
            body = (
                f"SELECT *, 1 AS {_SIDE}, "
                + ", ".join(f"{key} AS __k{index}" for index, key in enumerate(keys))
                + f" FROM read_parquet({_files(paths)}, union_by_name=true)"
            )
        else:
            body = (
                f"SELECT 1 AS {_SIDE}, "
                + ", ".join(f"NULL AS __k{index}" for index in range(len(fields)))
                + " WHERE false"
            )
        sides.append(f"{alias} AS ({body})")
    on = " AND ".join(
        f"n.__k{index} IS NOT DISTINCT FROM b.__k{index}" for index in range(len(fields))
    )
    reader = db.execute(
        "WITH " + ", ".join(sides) + f" SELECT n, b FROM n FULL OUTER JOIN b ON {on}"
    ).to_arrow_reader(batch_rows)
    hidden = {_SIDE, *(f"__k{index}" for index in range(len(fields)))}
    for batch in reader:
        if meter is not None:
            meter.take(batch.num_rows)
        for pair in batch.to_pylist():
            new, old = pair["n"], pair["b"]
            new = {k: v for k, v in new.items() if k not in hidden} if new and new[_SIDE] else None
            old = {k: v for k, v in old.items() if k not in hidden} if old and old[_SIDE] else None
            row_key = (
                _key(table, new, fields, baseline=False)
                if new is not None
                else _key(table, old or {}, fields, baseline=True)
            )
            yield from change_rows(table, row_key, old, new, changed_at=changed_at, run_id=run_id)


class DeltaRows:
    """Repeatable change discovery and stable external sorting for one build."""

    def __init__(self, path: Path, keys: tuple[str, ...]) -> None:
        self.path = path
        self.keys = keys
        self.conn = _open(path)
        try:
            self.conn.execute(
                "CREATE TABLE delta (position INTEGER PRIMARY KEY, ordering TEXT, row BLOB)"
            )
            self.conn.execute("CREATE INDEX delta_order ON delta(ordering,position)")
        except BaseException:
            self.close()
            raise

    def replace(self, rows: Iterable[Row]) -> None:
        self.conn.execute("DELETE FROM delta")
        self.conn.executemany(
            "INSERT INTO delta VALUES (?,?,?)",
            (
                (index, sort_key(row, self.keys), pickle.dumps(dict(row)))
                for index, row in enumerate(rows)
            ),
        )
        self.conn.commit()

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for (body,) in self.conn.execute("SELECT row FROM delta ORDER BY position"):
            yield pickle.loads(body)

    def sorted_rows(self) -> Iterator[dict[str, Any]]:
        for (body,) in self.conn.execute("SELECT row FROM delta ORDER BY ordering,position"):
            yield pickle.loads(body)

    def close(self) -> None:
        self.conn.close()
        self.path.unlink(missing_ok=True)


@contextmanager
def generated_delta(path: Path, keys: tuple[str, ...]) -> Iterator[DeltaRows]:
    delta = DeltaRows(path, keys)
    try:
        yield delta
    finally:
        delta.close()
