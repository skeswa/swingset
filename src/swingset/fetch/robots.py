"""24-hour robots cache; fetching is supplied by the same host gate."""

import gzip
import sqlite3
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from urllib.parse import urlsplit

import httpx
from protego import Protego

from swingset.clock import Clock
from swingset.fetch.archive import Archive


@dataclass(frozen=True)
class RobotsPolicy:
    allowed: bool
    crawl_delay: float = 0


class Robots:
    def __init__(self, connection: sqlite3.Connection, archive: Archive, clock: Clock) -> None:
        self.connection = connection
        self.archive = archive
        self.clock = clock

    def cached(
        self, url: str, *, maximum_bytes: int = 1024 * 1024
    ) -> tuple[RobotsPolicy | None, datetime | None]:
        """Bounded, verified cache read without fetch, recovery, or cache mutation."""
        host = urlsplit(url).hostname or ""
        row = self.connection.execute(
            "SELECT robots_sha256,robots_fetched_at,robots_status FROM hosts WHERE host=?", (host,)
        ).fetchone()
        if row is None or not row[0] or not row[1] or row[2] is None:
            return None, None
        try:
            fetched = datetime.fromisoformat(row[1])
            expires = fetched + timedelta(days=1)
            if fetched > self.clock.now() or expires <= self.clock.now():
                return None, expires
            path = self.archive.blob_path(row[0])
            if path.stat().st_size > maximum_bytes:
                return None, expires
            with gzip.open(path, "rb") as stream:
                body = stream.read(maximum_bytes + 1)
            if len(body) > maximum_bytes or sha256(body).hexdigest() != row[0]:
                return None, expires
            return _policy(url, int(row[2]), body), expires
        except (OSError, EOFError, ValueError, TypeError, zlib.error):
            return None, None

    def policy(self, url: str, fetch: Callable[[str], httpx.Response | None]) -> RobotsPolicy:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        conn = self.connection
        row = conn.execute(
            "SELECT robots_sha256,robots_fetched_at,robots_status FROM hosts WHERE host=?", (host,)
        ).fetchone()
        if (
            row is None
            or not row[1]
            or datetime.fromisoformat(row[1]) + timedelta(days=1) <= self.clock.now()
        ):
            response = fetch(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
            status = response.status_code if response is not None else 599
            body = response.content if response is not None else b""
            sha = self.archive.store_body(body)
            conn.execute("INSERT OR IGNORE INTO hosts(host) VALUES (?)", (host,))
            conn.execute(
                "UPDATE hosts SET robots_sha256=?,robots_fetched_at=?,robots_status=? WHERE host=?",
                (sha, self.clock.now().isoformat(), status, host),
            )
            conn.commit()
        else:
            status = int(row[2])
            body = self.archive.read_body(str(row[0]))
        result = _policy(url, status, body)
        delay = result.crawl_delay
        # Apply a newly learned delay to the request immediately following robots.
        cached = conn.execute(
            "SELECT robots_fetched_at,next_allowed_at FROM hosts WHERE host=?", (host,)
        ).fetchone()
        if cached and cached[0] and delay:
            due = datetime.fromisoformat(cached[0]) + timedelta(seconds=delay)
            if not cached[1] or datetime.fromisoformat(cached[1]) < due:
                conn.execute(
                    "UPDATE hosts SET next_allowed_at=? WHERE host=?", (due.isoformat(), host)
                )
        return result


def _policy(url: str, status: int, body: bytes) -> RobotsPolicy:
    if 400 <= status < 500:
        return RobotsPolicy(True)
    if status != 200:
        return RobotsPolicy(False)
    rules = Protego.parse(body.decode("utf-8", errors="replace"))
    return RobotsPolicy(
        bool(rules.can_fetch(url, "swingset")), float(rules.crawl_delay("swingset") or 0)
    )
