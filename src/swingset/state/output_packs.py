"""Derivation output packs and the one reader of a generation's rows (phase 4, WP2).

A generation's output is its label stream: for each row in ordinal order,
`canonical((table, key, payload)) + "\\n"`. Its sha256 is the label's
`output_digest`, so a `derivation_output` object holding exactly that stream
is named by the label, verifies against it, and is shared by every label with
the same output (F32).

A generation is **resident** when its row references are all in the file and
every payload they name is there, and **evicted** when a
`derivation_evicted` row says its references left for the pack. Complete
means resident or evicted; zero references alone is not partial (F33).

`rows`, `row` and `table_rows` are the readers every module outside the
allowlist uses (`tests/test_output_packs.py`): the `derivation_rows` view for
a generation that is not evicted, else the pack. `rows` checks the label and
raises `GenerationUnavailable` rather than return short or wrong rows; `row`
and `table_rows` are indexed lookups on the view, as before, and a verified
scan of an evicted generation's pack. `page_in`
puts an evicted generation's references back, a bounded number per call;
current pointers and building generations stay resident (F34), so a pointer
flip pages its target in first (`derivations.complete`, staged C).

Before schema 36 nothing is evicted and every read takes the view.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from itertools import islice

from . import objects
from .objects import ObjectUnavailable, Store

#: The object class of a generation's label stream.
CLASS = "derivation_output"


class GenerationUnavailable(RuntimeError):
    """A generation's rows are missing or do not match its label."""


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _line(table: str, key: str, payload: str) -> bytes:
    return _canonical((table, key, payload)).encode() + b"\n"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def available(conn: sqlite3.Connection) -> bool:
    """Whether this schema can evict (36 and later)."""
    return _has_table(conn, "derivation_evicted")


def label(conn: sqlite3.Connection, generation_id: str) -> tuple[str, int]:
    row = conn.execute(
        "SELECT output_digest,row_count FROM derivation_generations WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    if row is None:
        raise GenerationUnavailable(f"no label for generation {generation_id}")
    return str(row[0]), int(row[1])


def evicted(conn: sqlite3.Connection, generation_id: str) -> bool:
    """Whether the generation's references left the file for its pack."""
    if not available(conn):
        return False
    return (
        conn.execute(
            "SELECT 1 FROM derivation_evicted WHERE generation_id=?", (generation_id,)
        ).fetchone()
        is not None
    )


def _interned(conn: sqlite3.Connection) -> bool:
    return _has_table(conn, "derivation_row_refs")


def _stored(conn: sqlite3.Connection, generation_id: str) -> tuple[int, int]:
    """References in the file: (count, max ordinal + 1)."""
    table = "derivation_row_refs" if _interned(conn) else "derivation_rows"
    count, top = conn.execute(
        f"SELECT count(*),coalesce(max(ordinal)+1,0) FROM {table} WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    return int(count), int(top)


def resident(conn: sqlite3.Connection, generation_id: str) -> bool:
    """Every reference in the file, in ordinal order, and every payload they name."""
    _, rows = label(conn, generation_id)
    count, top = _stored(conn, generation_id)
    if count != rows or top != rows:
        return False
    if not _interned(conn):
        return True
    return not conn.execute(
        "SELECT EXISTS(SELECT 1 FROM derivation_row_refs r WHERE r.generation_id=? "
        "AND NOT EXISTS(SELECT 1 FROM derivation_payloads p "
        "WHERE p.payload_sha256=r.payload_sha256))",
        (generation_id,),
    ).fetchone()[0]


def complete(conn: sqlite3.Connection, generation_id: str) -> bool:
    """Resident or evicted: a generation whose output exists whole (F33)."""
    return evicted(conn, generation_id) or resident(conn, generation_id)


def _view(conn: sqlite3.Connection, generation_id: str) -> Iterator[tuple[str, str, str]]:
    for table, key, payload in conn.execute(
        "SELECT table_name,record_key,payload_json FROM derivation_rows "
        "WHERE generation_id=? ORDER BY ordinal",
        (generation_id,),
    ):
        yield str(table), str(key), str(payload)


def _decode(line: bytes, generation_id: str) -> tuple[str, str, str]:
    try:
        value = json.loads(line)
    except ValueError as error:
        raise GenerationUnavailable(f"pack of {generation_id} has a bad line") from error
    if (
        not isinstance(value, list)
        or len(value) != 3
        or not all(isinstance(item, str) for item in value)
    ):
        raise GenerationUnavailable(f"pack of {generation_id} has a bad line")
    return value[0], value[1], value[2]


def _pack(
    conn: sqlite3.Connection, generation_id: str, output_digest: str
) -> Iterator[tuple[str, str, str]]:
    """The pack's rows; the object store checks the digest when they run out."""
    try:
        store = objects.store_for(conn)
        for line in store.lines(CLASS, output_digest):
            yield _decode(line, generation_id)
    except (ObjectUnavailable, ValueError) as error:
        raise GenerationUnavailable(f"pack of {generation_id}: {error}") from error


def rows(
    conn: sqlite3.Connection,
    generation_id: str,
    *,
    expected: tuple[str, int] | None = None,
) -> Iterator[tuple[str, str, str]]:
    """A generation's rows `(table, key, payload_json)` in ordinal order.

    Rows stream as they are read; the label (``expected``, else the recorded
    one) is checked once they run out, so a caller acts on them only after
    reading to the end. A mismatch, a missing pack or a missing label raises
    `GenerationUnavailable`.
    """
    want = expected if expected is not None else label(conn, generation_id)
    source = (
        _pack(conn, generation_id, want[0])
        if evicted(conn, generation_id)
        else _view(conn, generation_id)
    )
    output = hashlib.sha256()
    count = 0
    for item in source:
        count += 1
        output.update(_line(*item))
        yield item
    if (output.hexdigest(), count) != want:
        raise GenerationUnavailable(f"generation {generation_id} does not match its label")


def row(
    conn: sqlite3.Connection,
    generation_id: str,
    table: str,
    key: str,
    *,
    is_evicted: bool | None = None,
) -> str | None:
    """One row's payload, or None when the generation has no such row.

    A generation that is not evicted is one indexed lookup (no label check,
    as before: the caller verifies the generation through `rows`). An evicted
    one is a scan of its pack, verified whole before the answer is returned.
    ``is_evicted`` skips the lookup for a caller that already knows.
    """
    if is_evicted is None:
        is_evicted = evicted(conn, generation_id)
    if not is_evicted:
        found = conn.execute(
            "SELECT payload_json FROM derivation_rows WHERE generation_id=? "
            "AND table_name=? AND record_key=?",
            (generation_id, table, key),
        ).fetchone()
        return None if found is None else str(found[0])
    result: str | None = None
    for name, record, payload in rows(conn, generation_id):
        if result is None and name == table and record == key:
            result = payload
    return result


def table_rows(
    conn: sqlite3.Connection, generation_id: str, table: str
) -> Iterator[tuple[str, str]]:
    """One table's rows `(key, payload_json)` of a generation, in ordinal order.

    Like `row`: an indexed read of a generation that is not evicted (the
    caller verifies through `rows` when it needs to), a verified pack scan
    of an evicted one.
    """
    if not evicted(conn, generation_id):
        for key, payload in conn.execute(
            "SELECT record_key,payload_json FROM derivation_rows WHERE generation_id=? "
            "AND table_name=? ORDER BY ordinal",
            (generation_id, table),
        ):
            yield str(key), str(payload)
        return
    found = [(key, payload) for name, key, payload in rows(conn, generation_id) if name == table]
    yield from found


def packed(conn: sqlite3.Connection, generation_id: str) -> bool:
    """Whether the generation's pack file is on disk (not verified)."""
    try:
        store = objects.store_for(conn)
    except ValueError:
        return False
    return store.exists(CLASS, label(conn, generation_id)[0])


def pack(
    conn: sqlite3.Connection,
    store: Store,
    generation_id: str,
    *,
    now: datetime | None = None,
    repair: bool = False,
) -> str:
    """Write the generation's pack and its `objects` row; return its digest.

    Idempotent: an indexed pack whose file is there is kept, unchecked; a
    caller verifies it. With `repair` the pack is written again from the
    resident rows even so, and `put_stream` replaces a damaged file. The rows
    stream from the view and are never held. A stream whose digest or line
    count is not the label's raises before anything is indexed.
    """
    output_digest, row_count = label(conn, generation_id)
    indexed = (
        conn.execute(
            "SELECT 1 FROM objects WHERE class=? AND digest=?", (CLASS, output_digest)
        ).fetchone()
        is not None
    )
    if indexed and store.exists(CLASS, output_digest) and not repair:
        return output_digest
    if evicted(conn, generation_id):
        if repair:
            raise GenerationUnavailable(
                f"evicted generation {generation_id} cannot repair its pack from the file"
            )
        if not store.exists(CLASS, output_digest):
            raise GenerationUnavailable(f"evicted generation {generation_id} has no pack")
        size = store.verify(CLASS, output_digest)
        stored = store.path(CLASS, output_digest).stat().st_size
        objects.record(conn, CLASS, output_digest, size, stored, now or datetime.now(UTC))
        return output_digest
    if not resident(conn, generation_id):
        raise GenerationUnavailable(f"generation {generation_id} is not resident")
    lines = 0

    def stream() -> Iterator[bytes]:
        nonlocal lines
        for item in _view(conn, generation_id):
            lines += 1
            yield _line(*item)

    written, raw_bytes, stored_bytes = store.put_stream(CLASS, stream())
    if written != output_digest or lines != row_count:
        raise GenerationUnavailable(f"generation {generation_id} does not match its label")
    objects.record(conn, CLASS, written, raw_bytes, stored_bytes, now or datetime.now(UTC))
    return written


def page_in(
    conn: sqlite3.Connection,
    generation_id: str,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Put back up to ``limit`` references of an evicted generation; True when whole.

    References go back in ordinal order with their payloads, so a stopped
    page-in resumes after the last stored ordinal. Each call reads the whole
    pack and checks it against the label before its transaction can commit.
    When every reference is back the generation is read back through the view
    and its eviction marker is deleted under the storage authority, in the
    caller's transaction. A generation that is not evicted is whole already.
    """
    if not conn.in_transaction:
        raise ValueError("page-in must share the caller's transaction")
    if not evicted(conn, generation_id):
        return True
    output_digest, row_count = label(conn, generation_id)
    count, start = _stored(conn, generation_id)
    if count != start:
        raise GenerationUnavailable(f"generation {generation_id} has references out of order")
    stop = row_count if limit is None else min(row_count, start + max(1, limit))
    source = rows(conn, generation_id, expected=(output_digest, row_count))
    for ordinal, (table, key, payload) in enumerate(islice(source, stop), 0):
        if ordinal < start:
            continue
        payload_sha256 = hashlib.sha256(payload.encode()).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)", (payload_sha256, payload)
        )
        conn.execute(
            "INSERT INTO derivation_row_refs VALUES (?,?,?,?,?)",
            (generation_id, ordinal, table, key, payload_sha256),
        )
    # Read the rest so the pack is checked against its label before commit.
    for _ in source:
        pass
    if stop < row_count:
        return False
    # Whole: the view must reproduce the label before the marker goes.
    output = hashlib.sha256()
    lines = 0
    for item in _view(conn, generation_id):
        lines += 1
        output.update(_line(*item))
    if (output.hexdigest(), lines) != (output_digest, row_count):
        raise GenerationUnavailable(f"paged-in generation {generation_id} does not match its label")
    conn.execute(
        "INSERT INTO storage_eviction_authority(singleton,reason,granted_at) VALUES (1,?,?)",
        ("page-in " + generation_id, objects.stamp(now or datetime.now(UTC))),
    )
    try:
        conn.execute("DELETE FROM derivation_evicted WHERE generation_id=?", (generation_id,))
    finally:
        conn.execute("DELETE FROM storage_eviction_authority")
    return True


def pack_intact(conn: sqlite3.Connection, generation_id: str) -> bool:
    """Whether the generation's pack file is there and hashes to its label."""
    try:
        objects.store_for(conn).verify(CLASS, label(conn, generation_id)[0])
    except ObjectUnavailable:
        return False
    return True


def rebuild(
    conn: sqlite3.Connection,
    generation_id: str,
    rows: Iterable[tuple[str, str, str]],
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Put back an evicted generation whose pack is lost, from rows that
    reproduce its label; True when whole.

    The repair path for a missing or damaged pack: a unit that reuses the
    generation computed the same output again. Like `page_in`, in the
    caller's transaction: references and payloads after the stored prefix,
    up to ``limit``, and the whole of ``rows`` is read so the label is
    checked before the transaction can commit. Once whole, the pack is
    written again from the file (`put_stream` replaces a damaged file), which
    repairs every label that shares it, and the marker goes under the storage
    authority.
    """
    if not conn.in_transaction:
        raise ValueError("a rebuild must share the caller's transaction")
    if not evicted(conn, generation_id):
        return True
    output_digest, row_count = label(conn, generation_id)
    count, start = _stored(conn, generation_id)
    if count != start:
        raise GenerationUnavailable(f"generation {generation_id} has references out of order")
    stop = row_count if limit is None else min(row_count, start + max(1, limit))
    output = hashlib.sha256()
    lines = 0
    for ordinal, (table, key, payload) in enumerate(rows):
        lines += 1
        output.update(_line(table, key, payload))
        if start <= ordinal < stop:
            payload_sha256 = hashlib.sha256(payload.encode()).hexdigest()
            conn.execute(
                "INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)", (payload_sha256, payload)
            )
            conn.execute(
                "INSERT INTO derivation_row_refs VALUES (?,?,?,?,?)",
                (generation_id, ordinal, table, key, payload_sha256),
            )
    if (output.hexdigest(), lines) != (output_digest, row_count):
        raise GenerationUnavailable(
            f"the output offered to rebuild {generation_id} does not match its label"
        )
    if stop < row_count:
        return False
    store = objects.store_for(conn)
    written, raw_bytes, stored_bytes = store.put_stream(
        CLASS, (_line(*item) for item in _view(conn, generation_id))
    )
    if written != output_digest:
        raise GenerationUnavailable(f"rebuilt generation {generation_id} does not match its label")
    objects.record(conn, CLASS, written, raw_bytes, stored_bytes, now or datetime.now(UTC))
    conn.execute(
        "INSERT INTO storage_eviction_authority(singleton,reason,granted_at) VALUES (1,?,?)",
        ("rebuild " + generation_id, objects.stamp(now or datetime.now(UTC))),
    )
    try:
        conn.execute("DELETE FROM derivation_evicted WHERE generation_id=?", (generation_id,))
    finally:
        conn.execute("DELETE FROM storage_eviction_authority")
    return True
