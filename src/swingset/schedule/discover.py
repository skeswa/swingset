"""Idempotent root and override watch discovery from captured inputs."""

from datetime import datetime
from urllib.parse import urlsplit

from swingset.schedule.watches import refresh_policy, upsert_watch
from swingset.sources import seed_watches
from swingset.sources.base import WatchSpec
from swingset.state.db import Database
from swingset.state.inputs import InputBundle


def discover(database: Database, bundle: InputBundle, now: datetime) -> int:
    specs = seed_watches(bundle.config, bundle.csv("source_urls.csv"))
    for row in bundle.csv("source_urls.csv"):
        source = row["source"]
        if source == "worlddanceregistry":
            source = "wdr"
        if not bundle.config.enabled(source):
            continue
        url = row["url"]
        if source == "wdr":
            key = urlsplit(url).path.strip("/").split("/")[0]
            source_ref = f"wdr:{key}"
        else:
            from swingset.sources.common import source_key

            source_ref = source_key(source, url)
        specs.append(
            WatchSpec(
                "",
                source,
                row.get("kind") or "event",
                "GET",
                url,
                row.get("parser") or f"{source}.rounds",
                source_ref=source_ref,
                notes=row.get("notes"),
            )
        )
    created = 0
    with database.transaction() as conn:
        for spec in specs:
            if bundle.config.enabled(spec.source):
                if upsert_watch(conn, spec, now):
                    created += 1
                    # New archived/active watches get their first fetch now; dormant waits.
                    refresh_policy(conn, bundle.config, spec.watch_id, now, jitter=0)
                    conn.execute(
                        "UPDATE watches SET next_check_at=? WHERE watch_id=? AND state NOT IN ('dormant','gone')",
                        (now.isoformat(), spec.watch_id),
                    )
    return created
