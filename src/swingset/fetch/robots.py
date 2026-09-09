"""24-hour robots cache; fetching is supplied by the same host gate."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
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
        if 400 <= status < 500:
            return RobotsPolicy(True)
        if status != 200:
            return RobotsPolicy(False)
        rules = Protego.parse(body.decode("utf-8", errors="replace"))
        delay = float(rules.crawl_delay("swingset") or 0)
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
        return RobotsPolicy(bool(rules.can_fetch(url, "swingset")), delay)
