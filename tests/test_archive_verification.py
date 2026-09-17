"""Streaming body verification preserves gzip/digest and recovery semantics."""

import gzip
import random
import zlib
from pathlib import Path

import pytest
from test_event_enumerations import admit_parent, child, finish_bootstrap, view
from test_event_enumerations import event as event

from swingset.fetch.archive import Archive, digest, durable_write
from swingset.schedule.event_inventory import inventory


def test_large_body_uses_bounded_compressed_and_decompressed_reads(tmp_path, monkeypatch):
    archive = Archive(tmp_path)
    body = random.Random(4).randbytes(3 * 1024 * 1024) + b"A" * (5 * 1024 * 1024)
    sha = archive.store_body(body)
    target = archive.blob_path(sha)
    original_open, original_read = Path.open, gzip.GzipFile.read
    compressed_reads, decompressed_reads = [], []

    class CheckedFile:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size=-1):
            assert 0 <= size <= 256 * 1024, "compressed body read was unbounded"
            compressed_reads.append(size)
            return self.stream.read(size)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

    def bounded_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return CheckedFile(stream) if path == target else stream

    def bounded_read(stream, size=-1):
        assert 0 <= size <= 64 * 1024, "decompressed body read was unbounded"
        decompressed_reads.append(size)
        return original_read(stream, size)

    def forbidden_whole_file(*args, **kwargs):
        raise AssertionError("verification must not materialize whole compressed contents")

    monkeypatch.setattr(Path, "open", bounded_open)
    monkeypatch.setattr(Path, "read_bytes", forbidden_whole_file)
    monkeypatch.setattr(gzip.GzipFile, "read", bounded_read)
    assert archive.verify_body(sha) is None
    assert len(decompressed_reads) > len(body) // (64 * 1024)
    assert len(compressed_reads) > 3


@pytest.mark.parametrize(
    "encoded,body",
    [
        (gzip.compress(b"first") + gzip.compress(b"second"), b"firstsecond"),
        (
            gzip.compress(b"first") + b"\0" * 17 + gzip.compress(b"second") + b"\0" * 9,
            b"firstsecond",
        ),
        (gzip.compress(b""), b""),
        (b"", b""),
    ],
)
def test_valid_gzip_forms_match_read_body(tmp_path, encoded, body):
    archive = Archive(tmp_path)
    sha = digest(body)
    durable_write(archive.blob_path(sha), encoded)
    assert archive.read_body(sha) == body
    assert archive.verify_body(sha) is None


@pytest.mark.parametrize(
    "damage",
    [
        "header",
        "deflate",
        "truncated_header",
        "truncated_footer",
        "bad_crc",
        "wrong_digest",
        "trailing_garbage",
    ],
)
def test_invalid_body_agrees_with_read_body_failure_class(tmp_path, damage):
    archive = Archive(tmp_path)
    body = b"retained historical body" * 40
    sha = digest(body)
    encoded = gzip.compress(body)
    if damage == "header":
        encoded = b"not gzip"
    elif damage == "deflate":
        encoded = encoded[:10] + b"\x07" + b"\0" * 8
    elif damage == "truncated_header":
        encoded = encoded[:7]
    elif damage == "truncated_footer":
        encoded = encoded[:-1]
    elif damage == "bad_crc":
        encoded = encoded[:-8] + bytes([encoded[-8] ^ 1]) + encoded[-7:]
    elif damage == "wrong_digest":
        encoded = gzip.compress(b"current replacement must not impersonate history")
    else:
        encoded += b"garbage"
    durable_write(archive.blob_path(sha), encoded)
    with pytest.raises((OSError, ValueError, EOFError, zlib.error)) as old:
        archive.read_body(sha)
    with pytest.raises(type(old.value)):
        archive.verify_body(sha)


@pytest.mark.parametrize("initial", ["missing", "corrupt"])
def test_verify_recovers_exact_body_once_and_rechecks(tmp_path, initial):
    body = b"only this historical digest"
    sha = digest(body)
    calls = []

    class Recovery:
        def recover(self, kind, requested, destination):
            calls.append((kind, requested, destination))
            durable_write(destination, gzip.compress(body))

    archive = Archive(tmp_path, recovery=Recovery())
    if initial == "corrupt":
        durable_write(archive.blob_path(sha), b"broken gzip")
    assert archive.verify_body(sha) is None
    assert calls == [("body", sha, archive.blob_path(sha))]
    assert archive.verify_body(sha) is None
    assert len(calls) == 1


def test_wrong_recovered_bytes_still_fail_without_retry_loop(tmp_path):
    calls = []

    class Recovery:
        def recover(self, kind, sha, destination):
            calls.append(sha)
            durable_write(destination, gzip.compress(b"wrong replacement"))

    archive = Archive(tmp_path, recovery=Recovery())
    sha = digest(b"expected historical content")
    with pytest.raises(ValueError, match="corrupt blob"):
        archive.verify_body(sha)
    assert calls == [sha]


def test_invalid_digest_does_not_enter_recovery(tmp_path):
    class Recovery:
        def recover(self, *args):
            raise AssertionError("invalid digest must not request recovery")

    with pytest.raises(ValueError, match="invalid artifact digest"):
        Archive(tmp_path, recovery=Recovery()).verify_body("not-a-digest")


def test_inventory_streams_body_checks_but_keeps_extract_validation_and_no_recovery(
    event, monkeypatch
):
    f = event
    admit_parent(f, ["one.htm"])
    child(f, "one.htm")
    finish_bootstrap(f)
    before = list(f.conn.iterdump())

    def forbidden_read(*args):
        raise AssertionError("body verification must not return full contents")

    monkeypatch.setattr(f.archive, "read_body", forbidden_read)
    assert view(f)["interpreted_pages"] == 1
    # Damaged body reopens the stage, without repairing files or changing SQLite.
    body_sha = f.conn.execute(
        "SELECT body_sha256 FROM snapshots WHERE snapshot_id='child-one.htm'"
    ).fetchone()[0]
    f.archive.blob_path(body_sha).unlink()
    actual = view(f)
    assert actual["interpreted_pages"] == actual["acquired_pages"] == 0
    assert list(f.conn.iterdump()) == before

    class Recovery:
        def recover(self, *args):
            raise AssertionError("doctor must not restore artifacts")

    with pytest.raises(ValueError, match="without recovery"):
        inventory(
            f.conn,
            Archive(f.db.state_dir, recovery=Recovery()),
            source="eepro",
            source_ref="eepro:test",
            now=f.corpus.clock.now(),
        )
