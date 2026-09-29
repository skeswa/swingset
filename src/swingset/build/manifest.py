"""Release manifests: what a partitioned release is built from (plan phase 3, WP7).

A release manifest (`release-manifest-v1`) names, sorted, every built
partition with its artifact digest and every withheld partition with its
reason, plus the inputs the assembly reads beside the artifacts: the digest
of the override files it reads, the build code scope and environment, the
baseline commit, the correction token and the `evidence_cutoff`, the newest
`created_at` among the generations and accepted source generations the
artifacts name (F24). It holds no clock, run id or path, so two builds from
the same pointers and inputs write the same bytes and the same digest.

The manifest is canonical JSON stored as `partitions/manifests/<digest>.json`
(digest = sha256 of the bytes) with a `release_manifests` row. A candidate
names it by digest in its `release_policy`; the build generation's context
names it too (D-0224).

`build/partitioned.py` builds stage 1 and the manifest, and loads the
fragments for the assembly. This module holds only the manifest's format,
files and tokens: the build generation and publication safety read it, so it
stays out of the stage-1 code's imports.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from swingset.state import release_store
from swingset.state.release_store import ManifestRow
from swingset.state.retention import manifest_file

from .closure_manifest import digest
from .files import canonical_json, durable_write, sha256_file

FORMAT = "release-manifest-v1"
SUPPORT_FORMAT = "partition-support-v1"
#: Override files the assembly reads beside the artifacts: suppressions are
#: applied to the rows, and the identity journal decides joins. Every other
#: override reaches a release through the project and link units.
ASSEMBLY_INPUTS = ("overrides/identity_overrides.csv", "overrides/suppressions.csv")
#: An artifact's sidecar file (`artifact_store.SIDECAR`).
SIDECAR = "sidecar.json"
#: Ids bound per `IN (...)` query.
_CHUNK = 500


class ManifestError(ValueError):
    """A release manifest or an artifact it names is missing or changed."""


@dataclass(frozen=True)
class ReleaseManifest:
    digest: str
    value: dict[str, Any]
    size: int
    #: The partition build's report (counts, seconds, peak memory); not stored.
    report: Mapping[str, Any] = field(default_factory=dict)
    #: Each built artifact's sidecar, read once for the manifest and the assembly.
    sidecars: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: The union of the artifacts' support: `[snapshot, parser, state, generations]`.
    support: tuple[tuple[Any, ...], ...] = ()

    @property
    def built(self) -> list[tuple[str, str]]:
        return [(str(name), str(artifact)) for name, artifact in self.value["built"]]

    @property
    def withheld(self) -> list[tuple[str, str]]:
        return [(str(name), str(reason)) for name, reason in self.value["withheld"]]


def support_digest(support: Iterable[Iterable[Any]]) -> str:
    """The digest of the artifacts' support: `[snapshot, parser, state, generations]`."""
    return digest(
        {
            "format": SUPPORT_FORMAT,
            "support": sorted([list(item) for item in support], key=json.dumps),
        }
    )


def policies_digest(conn: sqlite3.Connection) -> str:
    """The admission policies the support states were read under (as `closure._policy`)."""
    return digest(
        [dict(row) for row in conn.execute("SELECT * FROM admission_policies ORDER BY page_kind")]
    )


def support_token(conn: sqlite3.Connection, support: str) -> str:
    """The admission part of a partitioned release's correction token.

    The artifacts' support digest with the live admission policies and
    revocations, so a policy change or a revocation after the build changes
    it, as the closure's support token does.
    """
    from .closure import _revocations

    return digest(
        {
            "support": support,
            "policies": policies_digest(conn),
            "revocations": _revocations(conn),
        }
    )


def correction_token(conn: sqlite3.Connection, support: str) -> dict[str, Any]:
    """`identity_policy.correction_token` with the partitions' support as admission.

    ``support`` is the manifest's support digest. A public closure manifest
    carries only its support token, and the token function reads exactly
    that from it, so the same shape carries ours.
    """
    from .identity_policy import correction_token as token_of

    return token_of(
        conn,
        closure={
            "format": "release-closure-public-v1",
            "support_token": support_token(conn, support),
        },
    )


def overrides_digest(file_hashes: Mapping[str, str]) -> str:
    return digest({name: file_hashes.get(name) for name in ASSEMBLY_INPUTS})


def code_digests(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        str(name): str(value)
        for name, value in conn.execute(
            "SELECT input_name,digest FROM accepted_inputs WHERE consumer='pipeline' "
            "AND input_name IN ('recipe/env','recipe/code/build') ORDER BY input_name"
        )
    }


def _chunks(values: Iterable[str]) -> Iterator[list[str]]:
    ordered = sorted(set(values))
    for start in range(0, len(ordered), _CHUNK):
        yield ordered[start : start + _CHUNK]


def evidence_cutoff(
    conn: sqlite3.Connection, generations: Iterable[str], source_generations: Iterable[str]
) -> str | None:
    """The newest `created_at` among the named generations: the release's evidence time."""
    newest: datetime | None = None
    for table, ids in (
        ("derivation_generations", generations),
        ("source_generations", source_generations),
    ):
        for chunk in _chunks(ids):
            for (created,) in conn.execute(
                f"SELECT created_at FROM {table} WHERE generation_id IN "
                f"({','.join('?' * len(chunk))})",
                chunk,
            ):
                moment = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if newest is None or moment > newest:
                    newest = moment
    return None if newest is None else newest.isoformat()


def write(database: Any, value: Mapping[str, Any], *, now: datetime) -> tuple[str, int]:
    """Store a manifest's canonical bytes; return its digest and size."""
    body = canonical_json(value)
    manifest_digest = hashlib.sha256(body).hexdigest()
    store(database, body, manifest_digest, now=now)
    return manifest_digest, len(body)


def store(database: Any, body: bytes, manifest_digest: str, *, now: datetime) -> None:
    """Write the manifest file once, then record its row (the file first, as I8 wants)."""
    path = manifest_file(database.state_dir, manifest_digest)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != manifest_digest:
        durable_write(path, body)
    with database.transaction() as conn:
        existing = release_store.get(conn, ManifestRow, manifest_digest)
        release_store.upsert(
            conn,
            ManifestRow(
                manifest_digest,
                len(body),
                existing.created_at if existing is not None else now.isoformat(),
                existing.candidate_id if existing is not None else None,
                existing.verdict_json if existing is not None else None,
            ),
        )


def name_candidate(conn: sqlite3.Connection, manifest_digest: str, candidate_id: str) -> None:
    """Record which candidate a manifest built, in the caller's write."""
    existing = release_store.get(conn, ManifestRow, manifest_digest)
    if existing is None:
        raise ManifestError(f"release manifest row is missing: {manifest_digest}")
    release_store.upsert(
        conn,
        ManifestRow(
            existing.manifest_digest,
            existing.bytes,
            existing.created_at,
            candidate_id,
            existing.verdict_json,
        ),
    )


def read(state_dir: Path, manifest_digest: str) -> dict[str, Any]:
    """The manifest's content, once its bytes are shown to hash to its digest."""
    try:
        body = manifest_file(state_dir, manifest_digest).read_bytes()
    except (OSError, ValueError) as error:
        raise ManifestError(f"release manifest is unavailable: {manifest_digest}") from error
    if hashlib.sha256(body).hexdigest() != manifest_digest:
        raise ManifestError(f"release manifest changed: {manifest_digest}")
    value: dict[str, Any] = json.loads(body)
    if value.get("format") != FORMAT:
        raise ManifestError(f"not a release manifest: {manifest_digest}")
    return value


def verify(state_dir: Path, manifest_digest: str) -> dict[str, Any]:
    """The manifest, once it and every artifact it names are shown unchanged.

    Each artifact's sidecar bytes hash to its digest, and each table file
    hashes to what its sidecar says. Nothing here reads the database.
    """
    from swingset.state.retention import artifact_directory

    value = read(state_dir, manifest_digest)
    for partition, artifact in value["built"]:
        try:
            directory = artifact_directory(state_dir, str(artifact))
            body = (directory / SIDECAR).read_bytes()
            if hashlib.sha256(body).hexdigest() != artifact:
                raise ManifestError(f"partition artifact changed: {partition}")
            for name, entry in json.loads(body)["tables"].items():
                if sha256_file(directory / entry["file"]) != entry["sha256"]:
                    raise ManifestError(f"partition artifact file changed: {partition} {name}")
        except (OSError, ValueError, KeyError) as error:
            if isinstance(error, ManifestError):
                raise
            raise ManifestError(f"partition artifact is unavailable: {partition}") from error
    return value


__all__ = [
    "FORMAT",
    "ManifestError",
    "ReleaseManifest",
    "correction_token",
    "read",
    "support_digest",
    "verify",
    "write",
]
