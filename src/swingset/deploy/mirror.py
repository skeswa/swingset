"""Create one private, history-preserving rehearsal mirror in a trusted host job.

An owner token may bootstrap the new repository. It never becomes an application
credential. Any possible duplicate attempt is final: inspect state, never retry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shlex
import stat
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

SOURCE = "skeswa/swingset"
API = "https://huggingface.co/api/"
MAXIMUM_BYTES = 10 * 1024**3
FORMAT = "swingset-rehearsal-mirror-v1"


class Refused(ValueError):
    """A bootstrap guard failed; exception details never enter its receipt."""


def require(condition: object) -> None:
    if not condition:
        raise Refused("mirror bootstrap refused")


def private_file(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid())
        require(stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 65536)
        return stream.read(65537)


def policy(path: Path) -> tuple[dict[str, Any], str]:
    body = private_file(path)
    value = json.loads(body)
    require(
        set(value)
        == {
            "format",
            "source",
            "target",
            "source_head",
            "baseline",
            "source_commits",
            "maximum_source_bytes",
        }
    )
    require(value["format"] == FORMAT and value["source"] == SOURCE)
    require(
        isinstance(value["target"], str)
        and re.fullmatch(r"skeswa/swingset-rehearsal(?:-[a-z0-9]+)*", value["target"])
    )
    for name in ("source_head", "baseline"):
        require(isinstance(value[name], str) and re.fullmatch("[0-9a-f]{40}", value[name]))
    require(type(value["source_commits"]) is int and 0 < value["source_commits"] <= 10000)
    require(
        type(value["maximum_source_bytes"]) is int
        and 0 < value["maximum_source_bytes"] <= MAXIMUM_BYTES
    )
    return value, hashlib.sha256(body).hexdigest()


def token(path: Path) -> str:
    selected = None
    for line in private_file(path).decode().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        if name.strip() == "HF_REHEARSAL_TOKEN":
            require(selected is None)
            parts = shlex.split(value, comments=True)
            require(len(parts) == 1)
            selected = parts[0]
    require(isinstance(selected, str) and re.fullmatch("hf_[A-Za-z0-9]{8,}", selected))
    assert isinstance(selected, str)
    return selected


class Hub:
    def __init__(self, client: httpx.Client, target: str) -> None:
        self.client, self.target = client, target
        self.started, self.last = time.monotonic(), 0.0

    def request(self, method: str, url: str) -> httpx.Response:
        parsed = urlsplit(url)
        require(
            parsed.scheme == "https"
            and parsed.hostname == "huggingface.co"
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
        )
        require(
            method == "GET" or method == "POST" and url == API + "datasets/" + SOURCE + "/duplicate"
        )
        remaining = 600 - (time.monotonic() - self.started)
        require(remaining > 0)
        time.sleep(max(0, 2.1 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        body = {"repository": self.target, "visibility": "private"} if method == "POST" else None
        return self.client.request(method, url, timeout=min(120, remaining), json=body)

    def get(self, suffix: str) -> Any:
        response = self.request("GET", API + suffix)
        require(response.status_code == 200)
        return response.json()

    def info(self, repository: str, revision: str | None = None) -> dict[str, Any]:
        suffix = "datasets/" + repository + ("/revision/" + revision if revision else "")
        value: dict[str, Any] = self.get(suffix)
        require(value.get("id") == repository)
        return value

    def commits(self, repository: str) -> list[str]:
        prefix = API + "datasets/" + repository + "/commits/main"
        url, identifiers = prefix, []
        for _ in range(200):
            require(url.startswith(prefix))
            response = self.request("GET", url)
            require(response.status_code == 200)
            rows = response.json()
            require(isinstance(rows, list))
            for row in rows:
                require(isinstance(row["id"], str) and re.fullmatch("[0-9a-f]{40}", row["id"]))
                identifiers.append(row["id"])
            link = response.links.get("next")
            if not link:
                return identifiers
            url = link["url"]
        raise Refused("too many history pages")

    def refs(self, repository: str) -> dict[str, Any]:
        value = self.get("datasets/" + repository + "/refs")
        return {
            kind: sorted(
                [
                    {"name": row["name"], "targetCommit": row["targetCommit"]}
                    for row in value.get(kind, [])
                ],
                key=lambda row: row["name"],
            )
            for kind in ("branches", "tags")
        }


def copy(hub: Hub, configured: dict[str, Any], report: dict[str, Any]) -> None:
    report["phase"] = "owner identity"
    actor = hub.get("whoami-v2")
    require(actor.get("name") == "skeswa")
    access = actor.get("auth", {}).get("accessToken", {})
    fine = access.get("fineGrained", {})
    scopes = fine.get("scoped", [])
    require(access.get("role") == "fineGrained" and fine.get("global") == [] and len(scopes) == 1)
    entity = scopes[0].get("entity", {})
    require(
        entity.get("type") == "user" and entity.get("_id") == (actor.get("_id") or actor.get("id"))
    )
    require(
        set(scopes[0].get("permissions", []))
        == {"repo.content.read", "repo.access.read", "repo.contribute.write", "repo.write"}
    )
    report["phase"] = "fresh target"
    require(hub.request("GET", API + "datasets/" + hub.target).status_code == 404)
    report["phase"] = "source bounds"
    source = hub.info(SOURCE)
    require(source.get("private") is False and source.get("sha") == configured["source_head"])
    used = hub.get("datasets/" + SOURCE + "?expand%5B%5D=usedStorage").get("usedStorage")
    require(type(used) is int and 0 < used <= configured["maximum_source_bytes"])
    identifiers = hub.commits(SOURCE)
    require(
        len(identifiers) == configured["source_commits"]
        and identifiers[0] == configured["source_head"]
        and configured["baseline"] in identifiers
    )
    references = hub.refs(SOURCE)
    report.update(
        source_head=source["sha"],
        source_storage_bytes=used,
        source_commit_count=len(identifiers),
        source_history_sha256=hashlib.sha256(json.dumps(identifiers).encode()).hexdigest(),
    )
    report["phase"] = "duplicate once"
    report["mutation_attempted"] = True
    response = hub.request("POST", API + "datasets/" + SOURCE + "/duplicate")
    report["duplicate_http_status"] = response.status_code
    require(200 <= response.status_code < 300)
    report["phase"] = "target verification"
    destination = hub.info(hub.target)
    require(
        destination.get("private") is True and destination.get("sha") == configured["source_head"]
    )
    require(hub.commits(hub.target) == identifiers and hub.refs(hub.target) == references)
    baseline = hub.info(hub.target, configured["baseline"])
    require(baseline.get("sha") == configured["baseline"] and len(baseline.get("siblings", [])) > 1)
    report["phase"] = "source preservation"
    require(hub.info(SOURCE).get("sha") == source["sha"])
    report.update(
        complete=True,
        target=hub.target,
        target_private=True,
        all_source_history_equal=True,
        all_manual_refs_equal=True,
        baseline_present=True,
        baseline_files=len(baseline["siblings"]),
        source_head_unchanged=True,
        baseline_downloads_pending=True,
        application_token_pending=True,
    )


def run(configured: Path, credentials: Path, result: Path) -> dict[str, Any]:
    require(sys.platform == "linux")
    require(result.is_absolute() and result.parent.resolve() == result.parent)
    parent = result.parent.stat()
    require(parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700)
    # Reserve the receipt before secret reads or network effects. Never overwrite it.
    descriptor = os.open(result, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    report: dict[str, Any] = {
        "format": FORMAT,
        "at": datetime.now(UTC).isoformat(),
        "complete": False,
        "mutation_attempted": False,
        "phase": "policy",
        "credential_values_emitted_or_hashed": False,
    }
    started = time.monotonic()
    prior_logging = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        value, digest = policy(configured)
        report["policy_sha256"] = digest
        report["phase"] = "credentials"
        bearer = token(credentials)
        headers = {
            "Authorization": "Bearer " + bearer,
            "User-Agent": f"swingset/{version('swingset')} (+https://github.com/skeswa/swingset)",
        }
        with httpx.Client(headers=headers, follow_redirects=False, trust_env=False) as client:
            copy(Hub(client, value["target"]), value, report)
    except BaseException as error:
        report.update(error_type=type(error).__name__, no_automatic_retry=True)
    finally:
        logging.disable(prior_logging)
        report.update(
            seconds=round(time.monotonic() - started, 3), finished_at=datetime.now(UTC).isoformat()
        )
        with os.fdopen(descriptor, "w") as stream:
            stream.write(json.dumps(report, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return report


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swingset mirror-bootstrap")
    arguments(parser)
    args = parser.parse_args(argv)
    try:
        report = run(args.policy, args.credentials_file, args.result)
    except Exception as error:
        report = {
            "complete": False,
            "error_type": type(error).__name__,
            "mutation_attempted": None,
            "mutation_outcome": "unknown",
            "no_automatic_retry": True,
        }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
