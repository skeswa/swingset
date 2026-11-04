"""Free space as the worker's disk quota sees it (architecture phase 4).

A btrfs quota caps the worker's disk, and statfs ignores quotas, so
`shutil.disk_usage` reports the host volume, hundreds of GB more than the
machine may use. Root's `swingset-disk` (nix/module.nix) caches the quota as
`USED CAP [READ_AT]` in `/run/swingset-disk`; every room check reads the
smaller of that and statfs. A machine without the file (the Mac, tests) has
statfs only.
"""

from __future__ import annotations

import contextlib
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from swingset.state import engine

from .engine.usage import database_usage

#: Root's cached reading of the btrfs quota.
QUOTA_FILE = Path("/run/swingset-disk")
#: The watchdog refreshes the reading every 30 minutes; older is unknown.
STALE = timedelta(hours=1)
#: What an operator runs to refresh a stale reading.
REFRESH = "sudo swingset-disk"


def quota(path: Path | None = None) -> tuple[int, str | None] | None:
    """Free bytes under the quota and when they were read, or None without one."""
    try:
        fields = (path or QUOTA_FILE).read_text().split()
        used, cap = int(fields[0]), int(fields[1])
    except (OSError, ValueError, IndexError):
        return None
    return cap - used, fields[2] if len(fields) > 2 else None


def stale(read_at: str | None, now: datetime) -> bool:
    """A reading older than `STALE` is unknown; one with no time is taken as is."""
    if read_at is None:
        return False
    try:
        moment = datetime.fromisoformat(read_at.replace("Z", "+00:00"))
    except ValueError:
        try:
            moment = datetime.fromtimestamp(float(read_at), UTC)
        except ValueError:
            return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return now - moment > STALE


@dataclass(frozen=True)
class Room:
    """Free space under one directory, by statfs and by the quota."""

    statfs_free: int
    quota_free: int | None
    quota_read_at: str | None
    quota_stale: bool

    @property
    def free(self) -> int:
        """The smaller of the two; a stale quota reading still counts."""
        return (
            self.statfs_free if self.quota_free is None else min(self.statfs_free, self.quota_free)
        )

    def document(self) -> dict[str, Any]:
        return {
            "free_bytes": self.free,
            "statfs_free_bytes": self.statfs_free,
            "quota_free_bytes": self.quota_free,
            "quota_read_at": self.quota_read_at,
            "quota_stale": self.quota_stale,
        }


def room(directory: Path, *, now: datetime | None = None, quota_file: Path | None = None) -> Room:
    """Free space under `directory`, quota included when the machine has one."""
    reading = quota(quota_file)
    moment = now if now is not None else datetime.now(UTC)
    return Room(
        statfs_free=shutil.disk_usage(directory).free,
        quota_free=None if reading is None else reading[0],
        quota_read_at=None if reading is None else reading[1],
        quota_stale=reading is not None and stale(reading[1], moment),
    )


def free_bytes(directory: Path, *, quota_file: Path | None = None) -> int:
    """`room(directory).free`."""
    return room(directory, quota_file=quota_file).free


@contextlib.contextmanager
def fast_delete(conn: engine.Connection) -> Iterator[int]:
    """Keep the deletion context API; Postgres manages dead tuples through MVCC.

    Application delete authorities and immutable-row triggers still apply.
    There is no session secure-delete switch in Postgres.
    """
    yield 0


__all__ = [
    "fast_delete",
    "database_usage",
    "QUOTA_FILE",
    "REFRESH",
    "STALE",
    "Room",
    "free_bytes",
    "quota",
    "room",
    "stale",
]
