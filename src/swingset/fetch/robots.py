"""24-hour robots cache; fetching is supplied by the same host gate.

A read that failed (5xx, or 599 for no response) disallows the whole host
until it is read again after the host's own ServerError backoff, not after a
day (D-0242).
"""

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

DAY = timedelta(days=1)


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
            "SELECT robots_sha256,robots_fetched_at,robots_status,pause_streak FROM hosts "
            "WHERE host=?",
            (host,),
        ).fetchone()
        if row is None or not row[0] or not row[1] or row[2] is None:
            return None, None
        try:
            fetched = datetime.fromisoformat(row[1])
            expires = retry_at(fetched, int(row[2]), int(row[3] or 0))
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
            "SELECT robots_sha256,robots_fetched_at,robots_status,pause_streak FROM hosts "
            "WHERE host=?",
            (host,),
        ).fetchone()
        if (
            row is None
            or not row[1]
            or row[2] is None
            or retry_at(datetime.fromisoformat(row[1]), int(row[2]), int(row[3] or 0))
            <= self.clock.now()
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


def failed(status: int) -> bool:
    """A server error or no response (599): the reads retried on the backoff.

    Any other status that is neither a 200 nor a 4xx (1xx, 204, 304, ...) also
    disallows all, but is kept a day like a good read: it never raises the
    host's streak, so a short retry would re-read it every 15 minutes.
    """
    return status >= 500


def retry_at(fetched: datetime, status: int, streak: int) -> datetime:
    """When a robots read from ``fetched`` is read again.

    A read is kept a day, except a failed one (`failed`), which disallows
    all until the host's ServerError backoff ends: 15 minutes,
    doubling with the host's `pause_streak` up to a day (RFC 9309 2.3.1.4).
    The gate raised the streak when it released the failed request, so the
    retry is computed from the hosts row and nothing more is stored.
    """
    if not failed(status):
        return fetched + DAY
    return fetched + min(DAY, timedelta(seconds=900 * 2 ** min(max(streak, 1) - 1, 7)))


def blocked(connection: sqlite3.Connection, now: datetime) -> dict[str, datetime]:
    """Hosts whose last robots read failed, each with when it is read again.

    One query; the fair scheduler leaves these hosts out until then, so a
    failed read costs no pass time per watch.
    """
    held: dict[str, datetime] = {}
    for host, fetched, status, streak in connection.execute(
        "SELECT host,robots_fetched_at,robots_status,pause_streak FROM hosts "
        "WHERE robots_fetched_at IS NOT NULL AND robots_status>=500"
    ):
        try:
            retry = retry_at(datetime.fromisoformat(fetched), int(status), int(streak or 0))
        except (ValueError, TypeError):
            continue
        if retry > now:
            held[str(host)] = retry
    return held


def _policy(url: str, status: int, body: bytes) -> RobotsPolicy:
    if 400 <= status < 500:
        return RobotsPolicy(True)
    if status != 200:
        return RobotsPolicy(False)
    rules = Protego.parse(body.decode("utf-8", errors="replace"))
    return RobotsPolicy(
        bool(rules.can_fetch(url, "swingset")), float(rules.crawl_delay("swingset") or 0)
    )
