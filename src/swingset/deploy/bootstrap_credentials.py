"""Stage private administrator files on the trusted host, outside application jobs.

Run this stdlib-only module with host python3. Only the pinned Postgres container
is inspected; credential values never enter arguments, environment or receipts.
The separate platform bootstrap job receives one of the resulting files read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote


def _identity(path: Path) -> dict[str, Any]:
    item = path.lstat()
    return {
        "path": str(path),
        "uid": item.st_uid,
        "device": item.st_dev,
        "inode": item.st_ino,
        "mode": stat.S_IMODE(item.st_mode),
    }


def _private_parent(directory: Path) -> None:
    parent = directory.parent
    if (
        os.getuid() == 0
        or not directory.is_absolute()
        or directory.resolve() != directory
        or os.path.lexists(directory)
        or parent.is_symlink()
    ):
        raise RuntimeError("Administrator staging requires a fresh private directory")
    metadata = parent.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RuntimeError("Administrator staging parent ownership or mode differs")


def _write(path: Path, body: bytes) -> None:
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb"
    ) as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def _sync(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _postgres(container_id: str, image_id: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["docker", "container", "inspect", container_id],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        values = json.loads(result.stdout)
        if not isinstance(values, list) or len(values) != 1:
            raise ValueError
        container: dict[str, Any] = values[0]
        state = container["State"]
        if (
            container["Id"] != container_id
            or container["Image"] != image_id
            or state["Running"] is not True
            or state["Status"] != "running"
            or state.get("OOMKilled")
            or state.get("Paused")
            or state.get("Restarting")
        ):
            raise ValueError
        return container
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("Pinned Postgres container identity or running state differs") from None


def stage(
    container_id: str, image_id: str, directory: Path, databases: list[str]
) -> dict[str, Any]:
    """Create once, preserve partial files on failure, and report identities only."""
    if (
        re.fullmatch(r"[0-9a-f]{64}", container_id) is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
    ):
        raise RuntimeError("Administrator staging requires immutable Postgres identities")
    if (
        not databases
        or len(databases) != len(set(databases))
        or any(
            database != "postgres" and re.fullmatch(r"swingset[a-z0-9_]{0,47}", database) is None
            for database in databases
        )
    ):
        raise RuntimeError(
            "Administrator staging targets must be postgres or explicit Swingset databases"
        )
    _private_parent(directory)
    container = _postgres(container_id, image_id)
    environment: dict[str, str] = {}
    try:
        for entry in container["Config"].get("Env", []):
            key, value = entry.split("=", 1)
            if key in {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_PASSWORD_FILE"}:
                if key in environment:
                    raise ValueError
                environment[key] = value
        user, password = environment["POSTGRES_USER"], environment["POSTGRES_PASSWORD"]
        if (
            not user
            or not password
            or "POSTGRES_PASSWORD_FILE" in environment
            or len(user.encode()) > 63
            or len(password.encode()) > 4096
            or "\x00" in user
            or "\x00" in password
        ):
            raise ValueError
    except (AttributeError, ValueError, KeyError, TypeError):
        raise RuntimeError(
            "Pinned Postgres administrator credential metadata is missing or ambiguous"
        ) from None
    directory.mkdir(mode=0o700)
    owner = {
        "at": datetime.now(UTC).isoformat(),
        "postgres_container": container_id,
        "postgres_image": image_id,
        "directory": _identity(directory),
        "databases": databases,
    }
    _write(directory / "owner.json", (json.dumps(owner, sort_keys=True) + "\n").encode())
    _sync(directory.parent)
    files = []
    for database in databases:
        path = directory / (database + ".dsn")
        dsn = f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@127.0.0.1:5432/{database}\n"
        _write(path, dsn.encode())
        identity = _identity(path)
        if identity["uid"] != os.getuid() or identity["mode"] != 0o600:
            raise RuntimeError("Staged administrator file ownership or mode differs")
        files.append({**identity, "database": database, "present": True})
    result = {**owner, "files": files, "complete": True}
    _write(directory / "result.json", (json.dumps(result, sort_keys=True) + "\n").encode())
    _sync(directory)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-container", required=True)
    parser.add_argument("--postgres-image", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--database", action="append", required=True)
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            stage(
                arguments.postgres_container,
                arguments.postgres_image,
                arguments.directory,
                arguments.database,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
