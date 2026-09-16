#!/usr/bin/env python3
"""WP11's one bounded EEPro2019 event-index read; never insert child watches."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path

from swingset.clock import SystemClock
from swingset.config import SourceConfig, load_config
from swingset.fetch.archive import Archive, canonical, durable_write
from swingset.fetch.client import FetchClient
from swingset.fetch.wayback import replay_url, schedule_capture
from swingset.history.intake import intake_config
from swingset.sources import get_page_kind
from swingset.sources.base import ParseContext, WatchSpec
from swingset.state.db import open_database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    clock = SystemClock()
    page = get_page_kind("eepro.autoindex")
    url = "http://eepro.com/results/freedomswing2019/"
    archive_url = replay_url(url, "20190716024152")
    spec = WatchSpec(
        "",
        "eepro",
        "index",
        "GET",
        url,
        page.kind,
        source_ref="eepro:freedomswing2019",
        archive_url=archive_url,
    )
    config = intake_config(load_config(repo / "config"))
    config = replace(config, sources={**config.sources, "eepro": SourceConfig(True)})
    with open_database(args.state) as database:
        conn = database.connection
        archive = Archive(args.state)
        before = {
            row[0] for row in conn.execute("SELECT watch_id FROM watches WHERE kind!='index'")
        }
        run_id = database.start_run(clock.now(), dry_run=True)
        schedule_capture(conn, spec, now=clock.now())
        client = FetchClient(conn, config, clock, archive)
        try:
            fetched = client.fetch(
                spec.watch_id, page, run_id, deadline=clock.now() + timedelta(seconds=600)
            )
        finally:
            client.close()
        if fetched.snapshot_id is None:
            raise ValueError("WP11 event-index read did not return a snapshot")
        snapshot = dict(
            conn.execute(
                "SELECT * FROM snapshots WHERE snapshot_id=?", (fetched.snapshot_id,)
            ).fetchone()
        )
        body = archive.read_body(snapshot["body_sha256"])
        result = page.parse(
            page.extract(body),
            ParseContext(
                snapshot["snapshot_id"],
                spec.watch_id,
                url,
                "eepro",
                page.kind,
                spec.source_ref,
                snapshot["observed_at"],
            ),
        )
        after = {row[0] for row in conn.execute("SELECT watch_id FROM watches WHERE kind!='index'")}
        if after != before:
            raise ValueError("WP11 acceptance unexpectedly changed child watches")
        # Pure parsing above records locators only. This isolated acceptance
        # snapshot must not later run the ordinary child-watch insertion path.
        with database.transaction():
            conn.execute(
                "DELETE FROM pending_work WHERE stage='parse' AND unit_kind='snapshot' AND unit_id=?",
                (snapshot["snapshot_id"],),
            )
            conn.execute(
                "UPDATE watches SET state='sealed',next_check_at=NULL,notes='WP11 transport acceptance; children retained in review receipt only' WHERE watch_id=?",
                (spec.watch_id,),
            )
        args.output.mkdir(parents=True, exist_ok=True)
        durable_write(args.output / "freedomswing2019.html", body)
        receipt = {
            "snapshot": snapshot,
            "retained_cdx": "journal/evidence/collection/wayback-2026-09-11/cdx_eepro_results_all.json",
            "observations": [asdict(row) for row in result.observations],
            "discovered_child_locators": [asdict(row) for row in result.watches],
            "non_index_watches_before": len(before),
            "non_index_watches_after": len(after),
            "no_child_watches_created": before == after,
            "score_sheets_fetched": 0,
        }
        durable_write(args.output / "receipt.json", canonical(receipt))
        print(
            json.dumps(
                {
                    "snapshot_id": snapshot["snapshot_id"],
                    "children_recorded": len(result.watches),
                    "no_child_watches_created": before == after,
                    "body_bytes": len(body),
                }
            )
        )


if __name__ == "__main__":
    main()
