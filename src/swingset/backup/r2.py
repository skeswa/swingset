"""Immutable S3 backup objects; only a manifest makes an uploaded set visible."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from swingset.state.engine import Connection
from swingset.state.environment import EnvironmentRefused, ensure_external_allowed, identity

from .transport import (
    MAX_FILE_BYTES,
    TransportError,
    _relative,
    check_size,
    segment_path,
    set_files,
)


class R2Transport:
    def __init__(
        self, bucket: str, client: Any, *, connection: Connection | None, read_only: bool = False
    ) -> None:
        if not bucket or "/" in bucket:
            raise ValueError("invalid backup bucket")
        self.name = "r2:" + bucket
        self.bucket, self.client = bucket, client
        self.connection, self.read_only = connection, read_only
        if connection is None and not read_only:
            raise ValueError("a backup writer requires its live database connection")

    @contextmanager
    def bound(self, connection: Connection) -> Iterator[None]:
        previous = self.connection
        if (
            previous is not None
            and identity(previous).database_id != identity(connection).database_id
        ):
            raise EnvironmentRefused("Backup transport is attached to another database")
        self.connection = connection
        try:
            self._check("backup I/O")
            yield
        finally:
            self.connection = previous

    def _check(self, operation: str, *, write: bool = False) -> None:
        if write and self.read_only:
            raise TransportError("read-only backup credentials cannot publish")
        if self.connection is not None:
            target = self.client
            if identity(self.connection).environment == "dryrun":
                from swingset.deploy.rehearsal import BackupTarget
                from swingset.state.environment import FixtureIO

                if not isinstance(target, FixtureIO):
                    target = BackupTarget(str(self.client.meta.endpoint_url), self.bucket)
            ensure_external_allowed(self.connection, operation, target)
            self.connection.check_alive()

    def _listing(self) -> list[dict[str, Any]]:
        self._check("backup listing")
        rows = []
        try:
            for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket):
                rows.extend(page.get("Contents", []))
        except Exception:
            raise TransportError("backup bucket listing failed") from None
        return rows

    def segments(self) -> frozenset[str]:
        from .transport import _segments_of

        return _segments_of([row["Key"] for row in self._listing()])

    def sets(self) -> list[str]:
        from .transport import _sets_of

        return _sets_of([row["Key"] for row in self._listing()])

    def _put(self, local: Path, relative: str) -> None:
        from botocore.exceptions import ClientError

        relative = _relative(relative).as_posix()
        path = local / relative
        size = check_size(path)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        self._check("backup upload", write=True)
        try:
            with path.open("rb") as stream:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=relative,
                    Body=stream,
                    ContentLength=size,
                    Metadata={"sha256": digest},
                    IfNoneMatch="*",
                )
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise TransportError("backup object upload failed") from None
            # A previous attempt may have uploaded this object before dying.
            # Equal bytes are reusable; different bytes under this key are refused.
            self._check("backup existing object check")
            try:
                response = self.client.get_object(Bucket=self.bucket, Key=relative)
                with response["Body"] as body:
                    remote = hashlib.sha256()
                    count = 0
                    while block := body.read(1 << 20):
                        remote.update(block)
                        count += len(block)
                        if count > size:
                            raise TransportError("immutable backup object has excess bytes")
            except Exception:
                raise TransportError("backup existing object check failed") from None
            if count != size or remote.hexdigest() != digest:
                raise TransportError(
                    "immutable backup object already has different bytes"
                ) from None
        except Exception:
            raise TransportError("backup object upload failed") from None

    def publish(self, local: Path, set_id: str, segments: Sequence[str]) -> None:
        self._check("backup publication", write=True)
        for segment in sorted(set(segments)):
            self._put(local, segment_path(segment))
        for relative in set_files(local, set_id):
            self._put(local, relative)

    def fetch(self, relative: str, scratch: Path) -> Path:
        safe = _relative(relative)
        destination = scratch / safe
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name("." + destination.name + ".fetching")
        self._check("backup download")
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=safe.as_posix())
            with response["Body"] as body, temporary.open("wb") as output:
                count = 0
                while block := body.read(1 << 20):
                    count += len(block)
                    if count > MAX_FILE_BYTES:
                        raise TransportError("backup object exceeds the supported size")
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise TransportError("backup object download failed") from None
        return destination

    def collect(self) -> dict[str, Any]:
        # Retention and registry cleanup must protect all kept recovery images.
        # Upload credentials never perform an implicit remote deletion.
        rows = self._listing()
        return {
            "files": len(rows),
            "bytes": sum(int(row["Size"]) for row in rows),
            "removed": [],
            "retention": "operator policy",
        }


def configured(connection: Connection | None, *, read_only: bool = False) -> R2Transport:
    import boto3
    from botocore.config import Config

    endpoint = os.environ.get("SWINGSET_R2_ENDPOINT")
    bucket = os.environ.get("SWINGSET_R2_BUCKET")
    if not endpoint or not endpoint.startswith("https://") or not bucket:
        raise ValueError("SWINGSET_R2_ENDPOINT and SWINGSET_R2_BUCKET are required")
    if connection is not None:
        from swingset.deploy.rehearsal import BackupTarget

        ensure_external_allowed(
            connection, "backup transport configuration", BackupTarget(endpoint, bucket)
        )
    elif os.environ.get("SWINGSET_REHEARSAL_POLICY") or os.environ.get("SWINGSET_ENV") == "dryrun":
        from swingset.deploy.rehearsal import Policy

        policy = Policy.load()
        if endpoint.rstrip("/") != policy.backup_endpoint or bucket != policy.backup_bucket:
            raise EnvironmentRefused("backup destination differs from the rehearsal policy")
    access_key, secret_key = (
        os.environ.get("AWS_ACCESS_KEY_ID"),
        os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )
    if not access_key or not secret_key:
        raise ValueError("the backup job requires bucket-scoped S3 credentials")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="auto",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 2},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    return R2Transport(bucket, client, connection=connection, read_only=read_only)
