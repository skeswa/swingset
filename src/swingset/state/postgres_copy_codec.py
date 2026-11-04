"""Bounded, database-independent validation of the retained COPY binary format."""

from __future__ import annotations

import codecs
import hashlib
import math
import struct
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa

MAX_FIELD_BYTES = (1 << 30) - 1


def audit(
    path: Path,
    columns: Sequence[dict[str, Any]],
    *,
    collect: frozenset[str] = frozenset(),
    on_row: Callable[[dict[str, Any]], None] | None = None,
    on_text: Callable[[str, Iterable[bytes]], None] | None = None,
) -> tuple[int, str]:
    """Hash typed rows while rejecting malformed, incompatible or trailing bytes."""
    checksum = hashlib.sha256()
    count = 0
    with pa.input_stream(str(path), compression="zstd") as stream:

        def read(size: int) -> bytes:
            value = bytes(stream.read(size))
            if len(value) != size:
                raise ValueError("binary COPY stream is truncated")
            return value

        if read(11) != b"PGCOPY\n\xff\r\n\0":
            raise ValueError("binary COPY signature differs")
        flags, extension = struct.unpack(">ii", read(8))
        if flags != 0 or extension != 0:
            raise ValueError("binary COPY uses an unsupported header extension")
        while True:
            width = struct.unpack(">h", read(2))[0]
            if width == -1:
                if stream.read(1):
                    raise ValueError("binary COPY has bytes after its trailer")
                return count, checksum.hexdigest()
            if width != len(columns):
                raise ValueError("binary COPY row width differs from the retained schema")
            checksum.update(struct.pack(">Q", width))
            row: dict[str, Any] = {}
            for column in columns:
                size = struct.unpack(">i", read(4))[0]
                if size == -1:
                    if column.get("pk") or column.get("notnull"):
                        raise ValueError("binary COPY contains a NULL key or required field")
                    checksum.update(b"N" + struct.pack(">Q", 0))
                    if column["name"] in collect:
                        row[column["name"]] = None
                    continue
                if size < 0 or size > MAX_FIELD_BYTES:
                    raise ValueError("binary COPY has an invalid field length")
                kind = column["type"]
                if kind == "INTEGER":
                    if size != 8:
                        raise ValueError("binary COPY integer is not the retained int64 type")
                    value = str(struct.unpack(">q", read(8))[0]).encode("ascii")
                    checksum.update(b"I" + struct.pack(">Q", len(value)) + value)
                    if column["name"] in collect:
                        row[column["name"]] = int(value)
                elif kind == "REAL":
                    if size != 8:
                        raise ValueError("binary COPY real is not the retained float64 type")
                    value = read(8)
                    if not math.isfinite(struct.unpack(">d", value)[0]):
                        raise ValueError("binary COPY real is not finite")
                    checksum.update(b"F" + struct.pack(">Q", 8) + value)
                    if column["name"] in collect:
                        row[column["name"]] = struct.unpack(">d", value)[0]
                elif kind == "TEXT":
                    checksum.update(b"T" + struct.pack(">Q", size))

                    def blocks(size: int = size) -> Iterator[bytes]:
                        decoder = codecs.getincrementaldecoder("utf-8")("strict")
                        left = size
                        try:
                            while left:
                                block = read(min(left, 1 << 16))
                                if b"\0" in block:
                                    raise ValueError("binary COPY text contains NUL")
                                decoder.decode(block)
                                checksum.update(block)
                                left -= len(block)
                                yield block
                            decoder.decode(b"", final=True)
                        except UnicodeError:
                            raise ValueError("binary COPY text is not UTF-8") from None

                    values = blocks()
                    if column["name"] in collect:
                        if size > (1 << 20):
                            raise ValueError("a retained file identity exceeds its supported bound")
                        value_text = b"".join(values).decode("utf-8")
                        row[column["name"]] = value_text
                        if on_text is not None:
                            on_text(column["name"], [value_text.encode("utf-8")])
                    else:
                        if on_text is not None:
                            on_text(column["name"], values)
                        for _ in values:
                            pass
                else:
                    raise ValueError("binary COPY has an unknown retained column type")
            if on_row is not None:
                on_row(row)
            count += 1
