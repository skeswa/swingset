"""Bounded CDX refresh for event-list capture years beyond retained research."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from swingset.clock import Clock
from swingset.config import Config
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.history.catalog import CALENDAR_PATHS, Target, load_catalog, save_catalog
from swingset.history.intake import intake_config
from swingset.state.db import Database


def discover_calendar(
    database: Database,
    catalog_path: Path,
    *,
    config: Config,
    clock: Clock,
    first_year: int = 2024,
    wall_seconds: int = 900,
) -> int:
    client = FetchClient(
        database.connection, intake_config(config), clock, Archive(database.state_dir)
    )
    deadline = clock.now() + timedelta(seconds=wall_seconds)
    try:
        for year in range(first_year, clock.now().year + 1):
            for path in sorted(CALENDAR_PATHS):
                if clock.now() >= deadline:
                    break
                client.index_archive(
                    source="wsdc_calendar",
                    prefix="worldsdc.com" + path,
                    year=year,
                    deadline=deadline,
                )
    finally:
        client.close()
    targets = {target.target_id: target for target in load_catalog(catalog_path)}
    before = len(targets)
    for row in database.connection.execute(
        "SELECT url,timestamp,digest,cdx_query_id FROM archive_captures WHERE source='wsdc_calendar' AND status=200 ORDER BY timestamp,url"
    ):
        url = urlsplit(row[0])
        if url.query or url.path not in CALENDAR_PATHS:
            continue
        target = Target(
            "wsdc_calendar",
            str(row[0]),
            "wsdc_calendar.events",
            str(row[1]),
            str(row[2]),
            "archive_query:" + str(row[3]),
        )
        targets[target.target_id] = target
    save_catalog(
        catalog_path,
        tuple(sorted(targets.values(), key=lambda target: (target.timestamp or "", target.url))),
    )
    return len(targets) - before
