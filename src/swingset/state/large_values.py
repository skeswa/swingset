"""Bounded UTF-8 reads of retained manifest and recipe text on one snapshot."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import sql

from .engine import Connection


class ColumnReader:
    def __init__(self, conn: Connection, table: str, column: str, rowid: int) -> None:
        self.conn = conn
        self.table = sql.Identifier(*table.split("."))
        self.column = sql.Identifier(column)
        self.rowid = rowid
        self.offset = 0
        row = conn.execute(
            sql.SQL("SELECT octet_length({}) FROM {} WHERE rowid=%s").format(
                self.column, self.table
            ),
            (rowid,),
        ).fetchone()
        if row is None:
            raise KeyError("retained manifest row is missing")
        self.length = int(row[0] or 0)

    def __len__(self) -> int:
        return self.length

    def tell(self) -> int:
        return self.offset

    def seek(self, offset: int, whence: int = 0) -> int:
        base = (0, self.offset, self.length)[whence]
        destination = base + offset
        if destination < 0:
            raise ValueError("negative manifest offset")
        self.offset = destination
        return destination

    def read(self, size: int = -1) -> bytes:
        remaining = self.length - self.offset
        wanted = remaining if size < 0 else min(remaining, size)
        if wanted <= 0:
            return b""
        row = self.conn.execute(
            sql.SQL("SELECT ops.text_bytes({},%s,%s) AS body FROM {} WHERE rowid=%s").format(
                self.column, self.table
            ),
            (self.offset + 1, wanted, self.rowid),
        ).fetchone()
        if row is None:
            raise RuntimeError("retained manifest changed inside its snapshot")
        body = bytes(row[0])
        self.offset += len(body)
        return body


@contextmanager
def column_reader(
    conn: Connection, table: str, column: str, rowid: int, *, readonly: bool = True
) -> Iterator[ColumnReader]:
    if not readonly:
        raise ValueError("retained manifests are immutable")
    with conn.read_snapshot():
        yield ColumnReader(conn, table, column, rowid)
