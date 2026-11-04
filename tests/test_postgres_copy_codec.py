"""A compressed hash alone cannot certify an arbitrary binary COPY stream."""

from __future__ import annotations

import struct

import pyarrow as pa
import pytest

from swingset.state import postgres_copy_codec
from swingset.state.postgres_import import _row_digest, _scalar


def packed(path, body):
    with pa.output_stream(str(path), compression="zstd") as stream:
        stream.write(body)
    return path


def row(*values):
    return struct.pack(">h", len(values)) + b"".join(
        struct.pack(">i", -1) if value is None else struct.pack(">i", len(value)) + value
        for value in values
    )


HEADER = b"PGCOPY\n\xff\r\n\0" + struct.pack(">ii", 0, 0)
TRAILER = struct.pack(">h", -1)


def test_copy_offline_audit_matches_independent_scalar_hash(tmp_path):
    columns = [
        {"name": "id", "type": "INTEGER", "pk": 1},
        {"name": "text", "type": "TEXT"},
        {"name": "number", "type": "REAL"},
    ]
    values = (-7, "雪\t\\N\n" * 100_000, 2.5)
    body = (
        HEADER
        + row(struct.pack(">q", values[0]), values[1].encode(), struct.pack(">d", values[2]))
        + TRAILER
    )
    expected = _row_digest(
        iter(
            [
                [
                    _scalar(value, column["type"], "fixture")
                    for value, column in zip(values, columns, strict=True)
                ]
            ]
        )
    )
    assert postgres_copy_codec.audit(packed(tmp_path / "copy.zst", body), columns) == expected


@pytest.mark.parametrize(
    "field,kind,reason",
    [
        (b"wrong", "INTEGER", "int64"),
        (struct.pack(">d", float("nan")), "REAL", "finite"),
        (b"bad\0text", "TEXT", "NUL"),
        (b"\xff", "TEXT", "UTF-8"),
        (None, "TEXT", "NULL"),
    ],
)
def test_copy_offline_audit_rejects_incompatible_fields(tmp_path, field, kind, reason):
    with pytest.raises(ValueError, match=reason):
        postgres_copy_codec.audit(
            packed(tmp_path / "copy.zst", HEADER + row(field) + TRAILER),
            [{"name": "value", "type": kind, "pk": 1}],
        )


@pytest.mark.parametrize(
    "body", [b"bad", HEADER + struct.pack(">h", 2), HEADER + row(b"valid") + TRAILER + b"extra"]
)
def test_copy_offline_audit_rejects_bad_structure(tmp_path, body):
    with pytest.raises(ValueError):
        postgres_copy_codec.audit(
            packed(tmp_path / "copy.zst", body), [{"name": "value", "type": "TEXT"}]
        )
