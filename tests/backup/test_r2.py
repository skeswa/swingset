"""R2 upload ordering, restart reuse and environment isolation without a network."""

import io

import pytest
from botocore.exceptions import ClientError

from swingset.backup.r2 import R2Transport
from swingset.backup.transport import TransportError
from swingset.state.db import open_database
from swingset.state.environment import EnvironmentRefused, FixtureIO


class Bucket(FixtureIO):
    def __init__(self):
        self.objects = {}
        self.writes = []
        self.fail = None

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, **kwargs):
        yield {
            "Contents": [{"Key": key, "Size": len(value)} for key, value in self.objects.items()]
        }

    def put_object(self, **request):
        key = request["Key"]
        assert request["IfNoneMatch"] == "*"
        if key == self.fail:
            raise RuntimeError("private credentials must not appear in errors")
        if key in self.objects:
            raise ClientError(
                {
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                    "Error": {"Code": "PreconditionFailed"},
                },
                "PutObject",
            )
        self.objects[key] = request["Body"].read()
        self.writes.append(key)

    def get_object(self, **request):
        return {"Body": io.BytesIO(self.objects[request["Key"]])}


@pytest.fixture
def files(tmp_path):
    local = tmp_path / "local"
    for name, body in {
        "segments/" + "a" * 64 + ".tar": b"segment",
        "sets/set_20260101T000000Z/db/part.zst": b"copy",
        "sets/set_20260101T000000Z/index.jsonl.zst": b"index",
        "sets/set_20260101T000000Z/backup.json": b"manifest",
    }.items():
        path = local / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return local


def test_manifest_last_and_equal_objects_are_reused(files, tmp_path):
    bucket = Bucket()
    with open_database(tmp_path / "state") as database:
        transport = R2Transport("swingset-backups", bucket, connection=database.connection)
        transport.publish(files, "set_20260101T000000Z", ["a" * 64])
        assert bucket.writes[-1] == "sets/set_20260101T000000Z/backup.json"
        assert transport.sets() == ["set_20260101T000000Z"]
        assert transport.segments() == frozenset({"a" * 64})
        writes = list(bucket.writes)
        transport.publish(files, "set_20260101T000000Z", ["a" * 64])
        assert bucket.writes == writes
        assert (
            transport.fetch("sets/set_20260101T000000Z/backup.json", tmp_path / "read").read_bytes()
            == b"manifest"
        )
        assert transport.collect()["removed"] == []


def test_interrupted_upload_has_no_complete_set_and_leaks_no_error_context(files, tmp_path):
    bucket = Bucket()
    bucket.fail = "sets/set_20260101T000000Z/db/part.zst"
    with open_database(tmp_path / "state") as database:
        transport = R2Transport("swingset-backups", bucket, connection=database.connection)
        with pytest.raises(TransportError) as error:
            transport.publish(files, "set_20260101T000000Z", ["a" * 64])
        assert "private" not in str(error.value)
        assert transport.sets() == []
        bucket.fail = None
        transport.publish(files, "set_20260101T000000Z", ["a" * 64])
        bucket.objects["sets/set_20260101T000000Z/backup.json"] = b"changed"
        with pytest.raises(TransportError, match="different bytes"):
            transport.publish(files, "set_20260101T000000Z", ["a" * 64])


def test_real_client_is_refused_on_stored_dev_identity(tmp_path, monkeypatch):
    monkeypatch.delenv("SWINGSET_ENV", raising=False)
    with open_database(tmp_path / "state") as database:
        transport = R2Transport("swingset-backups", object(), connection=database.connection)
        with pytest.raises(EnvironmentRefused):
            transport.sets()
        read_only = R2Transport(
            "swingset-backups", Bucket(), connection=database.connection, read_only=True
        )
        with pytest.raises(TransportError, match="read-only"):
            read_only.publish(tmp_path, "unused", [])


def test_download_is_bounded_and_partial_file_is_removed(tmp_path, monkeypatch):
    from swingset.backup import r2

    bucket = Bucket()
    bucket.objects["sets/set_20260101T000000Z/backup.json"] = b"oversized"
    monkeypatch.setattr(r2, "MAX_FILE_BYTES", 4)
    with open_database(tmp_path / "state") as database:
        transport = R2Transport("swingset-backups", bucket, connection=database.connection)
        with pytest.raises(TransportError, match="download failed"):
            transport.fetch("sets/set_20260101T000000Z/backup.json", tmp_path / "read")
    assert not list((tmp_path / "read").rglob("*json*"))
