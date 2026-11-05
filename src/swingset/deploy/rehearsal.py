"""Explicit destinations for real I/O from an immutable dry-run database.

The policy is a read-only host mount. It supplements the host firewall and
repository/bucket-scoped credentials; it cannot replace either boundary.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from swingset.publish.huggingface import HuggingFaceHub
from swingset.state.environment import RehearsalIO

LIVE_REPOSITORIES = frozenset(
    {"skeswa/swingset", "skeswa/swingset-archive", "skeswa/swingset-backup-sets"}
)


@dataclass(frozen=True)
class Policy:
    database_ids: frozenset[str]
    dataset: str
    branch: str
    backup_bucket: str
    backup_endpoint: str
    proxy_url: str
    mock_origins: frozenset[str]

    @classmethod
    def load(cls) -> Policy:
        from swingset.state.environment import EnvironmentRefused

        try:
            path = Path(os.environ["SWINGSET_REHEARSAL_POLICY"])
            if path.stat().st_mode & 0o022:
                raise ValueError("policy is writable by another user")
            data = json.loads(path.read_text())
            identifiers = frozenset(str(UUID(value)) for value in data["database_ids"])
            dataset, branch, bucket = data["dataset"], data["branch"], data["backup_bucket"]
            endpoint, proxy = data["backup_endpoint"], data["proxy_url"]
            origins = frozenset(data["mock_origins"])
            if not identifiers or dataset in LIVE_REPOSITORIES:
                raise ValueError("missing identities or live repository")
            if not isinstance(dataset, str) or len(dataset.split("/")) != 2:
                raise ValueError("invalid rehearsal repository")
            if (
                not isinstance(branch, str)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/-]{0,120}", branch)
                or any(token in branch for token in ("..", "//", "/."))
                or branch.endswith(("/", ".", ".lock"))
            ):
                raise ValueError("invalid rehearsal branch")
            if not isinstance(bucket, str) or not bucket or "/" in bucket:
                raise ValueError("invalid rehearsal bucket")
            remote = urlsplit(endpoint)
            if remote.scheme != "https" or not remote.hostname or remote.path not in {"", "/"}:
                raise ValueError("invalid rehearsal object endpoint")
            if remote.username or remote.password or remote.query or remote.fragment:
                raise ValueError("invalid rehearsal object endpoint")
            gateway = urlsplit(proxy)
            if gateway.scheme != "http" or gateway.username or gateway.password:
                raise ValueError("invalid rehearsal proxy")
            if gateway.path not in {"", "/"} or gateway.query or gateway.fragment:
                raise ValueError("invalid rehearsal proxy")
            if not ipaddress.ip_address(gateway.hostname or "").is_private or not gateway.port:
                raise ValueError("the rehearsal proxy must be a private address")
            for value in origins:
                parsed = urlsplit(value)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.path not in {"", "/"}
                    or parsed.query
                    or parsed.fragment
                    or str(httpx.URL(value)).rstrip("/") != value.rstrip("/")
                ):
                    raise ValueError("invalid owned mock origin")
            return cls(identifiers, dataset, branch, bucket, endpoint.rstrip("/"), proxy, origins)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise EnvironmentRefused("a valid read-only rehearsal policy is required") from exc

    def require_database(self, identifier: str) -> None:
        from swingset.state.environment import EnvironmentRefused

        if str(UUID(identifier)) not in self.database_ids:
            raise EnvironmentRefused("database identity is absent from the rehearsal policy")


@dataclass(frozen=True)
class BackupTarget(RehearsalIO):
    endpoint: str
    bucket: str

    def authorize(self, database_id: str, operation: str) -> None:
        authorize(database_id, operation, self)


def authorize(identifier: str, operation: str, transport: object) -> None:
    from swingset.state.environment import EnvironmentRefused

    policy = Policy.load()
    policy.require_database(identifier)
    if (
        isinstance(transport, RehearsalHub)
        and transport.repo_id == policy.dataset
        and transport.revision == policy.branch
    ):
        if operation in {
            "publication",
            "publication reads",
            "restore public verification",
            "restore baseline download",
        }:
            return
    if isinstance(transport, RehearsalTransport) and operation == "source requests":
        return  # Every actual request and redirect passes handle_request below.
    if isinstance(transport, BackupTarget) and operation.startswith("backup "):
        if (
            transport.endpoint.rstrip("/") == policy.backup_endpoint
            and transport.bucket == policy.backup_bucket
        ):
            return
    raise EnvironmentRefused("this external effect is outside the rehearsal policy")


class RehearsalHub(HuggingFaceHub, RehearsalIO):
    """A repository-bound adapter; the normal journal still governs publication."""

    def __init__(self, *, token: str, api: Any = None) -> None:
        policy = Policy.load()
        super().__init__(policy.dataset, token=token, api=api, revision=policy.branch)

    def _require_target(self) -> None:
        from swingset.state.environment import EnvironmentRefused

        policy = Policy.load()
        if self.repo_id != policy.dataset or self.revision != policy.branch:
            raise EnvironmentRefused("rehearsal repository or branch changed after configuration")

    def authorize(self, database_id: str, operation: str) -> None:
        authorize(database_id, operation, self)

    def head(self) -> str | None:
        self._require_target()
        return super().head()

    def is_initial_head(self, commit: str) -> bool:
        self._require_target()
        return super().is_initial_head(commit)

    def download(self, name: str, commit: str) -> Path:
        self._require_target()
        return super().download(name, commit)

    def inspect(self, commit: str) -> Any:
        self._require_target()
        return super().inspect(commit)

    def create_commit(self, **arguments: Any) -> str:
        self._require_target()
        from swingset.state.environment import EnvironmentRefused

        from .scheduler import held

        if held(Path(os.environ.get("SWINGSET_HOLDS_DIR", "/run/swingset-holds"))):
            raise EnvironmentRefused("an out-of-band hold blocks rehearsal publication")
        return super().create_commit(**arguments)


class RehearsalTransport(httpx.BaseTransport, RehearsalIO):
    """The real fetch gate may reach only explicitly owned mock origins."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport or httpx.HTTPTransport(proxy=Policy.load().proxy_url)

    def authorize(self, database_id: str, operation: str) -> None:
        authorize(database_id, operation, self)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        from swingset.state.environment import EnvironmentRefused

        url = request.url
        origin = str(url.copy_with(path="", query=None, fragment=None)).rstrip("/")
        if url.userinfo or origin not in {
            value.rstrip("/") for value in Policy.load().mock_origins
        }:
            raise EnvironmentRefused("dry-run fetching is limited to owned mock origins")
        return self.transport.handle_request(request)

    def close(self) -> None:
        self.transport.close()
