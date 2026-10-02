"""The content-addressed object store (`state/objects.py`, architecture phase 4)."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from swingset.state import objects
from swingset.state.db import open_database
from swingset.state.objects import CLASSES, STUB, ObjectUnavailable, Store, store_for

NOW = datetime(2026, 10, 1, tzinfo=UTC)
LINES = [b'["event","e1","{}"]\n', b'["event","e2","{\\"a\\":1}"]\n', b"last"]


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def listing(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


@pytest.mark.parametrize("cls", sorted(objects.STORED))
def test_an_object_round_trips_by_stream_bytes_and_lines(tmp_path: Path, cls: str) -> None:
    store = Store(tmp_path)
    raw = b"".join(LINES)
    digest, raw_bytes, stored = store.put_stream(cls, iter(LINES))
    assert digest == sha(raw)
    assert raw_bytes == len(raw)
    path = store.path(cls, digest)
    assert path == tmp_path / "objects" / cls / digest[:2] / digest
    assert stored == path.stat().st_size
    assert store.exists(cls, digest)
    with store.open(cls, digest) as handle:
        assert handle.read() == raw
    assert list(store.lines(cls, digest)) == LINES
    assert store.verify(cls, digest) == len(raw)
    stored_bytes = path.read_bytes()
    if cls in objects.RAW:
        assert stored_bytes == raw
    else:
        # gzip with mtime 0 and no file name: equal bytes give an equal file.
        assert gzip.decompress(stored_bytes) == raw
        assert stored_bytes[4:8] == b"\0\0\0\0"
        assert not stored_bytes[3] & 0x08
    assert store.put_bytes(cls, raw) == (digest, stored)
    assert listing(tmp_path / "objects") == [f"{cls}/{digest[:2]}/{digest}"]


def test_a_release_manifest_is_raw_json(tmp_path: Path) -> None:
    store = Store(tmp_path)
    manifest = json.dumps({"partitions": {"event/e1": "a" * 64}}).encode()
    digest, _ = store.put_bytes("release_manifest", manifest)
    with store.path("release_manifest", digest).open() as handle:
        assert json.load(handle) == {"partitions": {"event/e1": "a" * 64}}


def test_the_empty_object_round_trips(tmp_path: Path) -> None:
    store = Store(tmp_path)
    digest, raw_bytes, _ = store.put_stream("derivation_output", iter(()))
    assert (digest, raw_bytes) == (sha(b""), 0)
    assert list(store.lines("derivation_output", digest)) == []
    assert store.verify("derivation_output", digest) == 0


def test_equal_bytes_are_stored_once_and_indexed_once(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        store = store_for(database.connection)
        first = store.put_stream("parse_body", [b"ab", b"c"])
        path = store.path("parse_body", first[0])
        inode = path.stat().st_ino
        second = store.put_stream("parse_body", [b"a", b"bc"])
        assert second == first
        assert path.stat().st_ino == inode
        with database.transaction() as conn:
            objects.record(conn, "parse_body", first[0], first[1], first[2], NOW)
            objects.record(conn, "parse_body", first[0], first[1], first[2], NOW)
        rows = database.connection.execute(
            "SELECT class,digest,raw_bytes,stored_bytes,stored_at FROM objects"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("parse_body", first[0], 3, first[2], "2026-10-01T00:00:00.000000Z")
        ]
    assert listing(state / "objects") == [f"parse_body/{first[0][:2]}/{first[0]}"]


def test_a_crash_before_the_rename_leaves_only_a_writing_file(tmp_path: Path) -> None:
    script = textwrap.dedent(
        f"""
        import os
        from pathlib import Path
        from swingset.state.objects import Store
        os.replace = lambda *args: os._exit(9)
        Store(Path({str(tmp_path)!r})).put_stream("derivation_output", [b"row\\n"] * 1000)
        """
    )
    result = subprocess.run([sys.executable, "-c", script], check=False)
    assert result.returncode == 9
    found = listing(tmp_path / "objects")
    assert len(found) == 1 and found[0].startswith(objects.WRITING_PREFIX)
    store = Store(tmp_path)
    digest = sha(b"row\n" * 1000)
    assert not store.exists("derivation_output", digest)
    with pytest.raises(ObjectUnavailable, match="missing"):
        store.verify("derivation_output", digest)
    # The next write completes beside the leftover, which the collector removes.
    assert store.put_stream("derivation_output", [b"row\n"] * 1000)[0] == digest
    assert store.verify("derivation_output", digest) == 4000


def test_a_failed_write_removes_its_temporary(tmp_path: Path) -> None:
    def chunks():
        yield b"partial"
        raise RuntimeError("source failed")

    store = Store(tmp_path)
    with pytest.raises(RuntimeError, match="source failed"):
        store.put_stream("label_body", chunks())
    assert listing(tmp_path / "objects") == []


@pytest.mark.parametrize("damage", ["flip", "truncate", "swap"])
def test_a_corrupt_object_raises_and_a_new_write_replaces_it(tmp_path: Path, damage: str) -> None:
    store = Store(tmp_path)
    raw = b"".join(LINES) * 200
    digest, _, _ = store.put_stream("derivation_output", [raw])
    path = store.path("derivation_output", digest)
    stored = bytearray(path.read_bytes())
    if damage == "flip":
        stored[len(stored) // 2] ^= 0xFF
        path.write_bytes(bytes(stored))
    elif damage == "truncate":
        path.write_bytes(bytes(stored[: len(stored) // 2]))
    else:
        path.write_bytes(gzip.compress(b"other bytes\n", mtime=0))
    with pytest.raises(ObjectUnavailable):
        store.verify("derivation_output", digest)
    with pytest.raises(ObjectUnavailable):
        list(store.lines("derivation_output", digest))
    store.put_stream("derivation_output", [raw])
    assert store.verify("derivation_output", digest) == len(raw)


def test_a_raw_object_with_wrong_bytes_raises(tmp_path: Path) -> None:
    store = Store(tmp_path)
    digest, _ = store.put_bytes("release_manifest", b'{"a":1}')
    store.path("release_manifest", digest).write_bytes(b'{"a":2}')
    with pytest.raises(ObjectUnavailable, match="does not match"):
        store.verify("release_manifest", digest)
    with pytest.raises(ObjectUnavailable, match="does not match"):
        list(store.lines("release_manifest", digest))


def test_names_are_checked(tmp_path: Path) -> None:
    store = Store(tmp_path)
    for cls in ("body", "extract", "partition_artifact", "dependency_set", "nope"):
        with pytest.raises(ValueError, match="not an object class"):
            store.put_bytes(cls, b"x")
    with pytest.raises(ValueError, match="not a sha256"):
        store.path("parse_body", "../../state.sqlite")
    assert STUB == '{"packed":true}'


def test_store_for_follows_a_copy_of_the_database(tmp_path: Path) -> None:
    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        store = store_for(database.connection)
        assert store.state_dir == state
        digest, raw_bytes, stored = store.put_stream("derivation_output", iter(LINES))
        with database.transaction() as conn:
            objects.record(conn, "derivation_output", digest, raw_bytes, stored, NOW)
        database.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    copy = tmp_path / "checkpoint" / "state"
    shutil.copytree(state, copy)
    shutil.rmtree(state)
    with open_database(copy, lock=False) as database:
        store = store_for(database.connection)
        assert store.state_dir == copy
        (listed,) = database.connection.execute("SELECT digest FROM objects").fetchall()
        assert list(store.lines("derivation_output", listed[0])) == LINES
    import sqlite3

    with pytest.raises(ValueError, match="database file"):
        store_for(sqlite3.connect(":memory:"))


def test_classes_mirror_the_seeded_rows(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        rows = database.connection.execute(
            "SELECT class,location,roots,collector,min_age_seconds,backed_up FROM blob_classes "
            "ORDER BY rowid"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (c.name, c.location, c.roots, c.collector, c.min_age_seconds, int(c.backed_up))
        for c in CLASSES.values()
    ]
    assert objects.STORED == {"derivation_output", "parse_body", "label_body", "release_manifest"}


def test_the_store_never_imports_pyarrow() -> None:
    script = "import sys, swingset.state.objects; assert 'pyarrow' not in sys.modules"
    subprocess.run([sys.executable, "-c", script], check=True)
