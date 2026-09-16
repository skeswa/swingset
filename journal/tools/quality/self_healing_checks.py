#!/usr/bin/env python3
"""Reproduce self-healing defects offline, using temporary state and mock HTTP."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.link.candidates import DancerRecord, Subject, generate_candidates
from swingset.link.score import score_candidate
from swingset.project.process import process_unit
from swingset.schedule.parse import parse_snapshot
from swingset.schedule.registry import advance_sweep, discover_registry
from swingset.schedule.watches import upsert_watch
from swingset.sources import get_page_kind
from swingset.sources.wsdc_registry.adapter import SOURCE
from swingset.state.db import open_database
from swingset.state.inputs import InputBundle
from swingset.state.work import WorkUnit


def probe_check(state: Path) -> dict[str, object]:
    fixture = (
        Path(__file__).resolve().parents[3]
        / "src/swingset/sources/wsdc_registry/fixtures/lookup-1000000.body"
    ).read_bytes()
    initial = datetime(2026, 9, 1, tzinfo=UTC)
    clock = FakeClock(initial)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(404, content=fixture)

    config = Config({"points.worldsdc.com": HostConfig()}, {"wsdc_registry": SourceConfig(True)})
    with open_database(state, lock=False) as database:
        conn = database.connection
        run = database.start_run(clock.now(), dry_run=True)
        watch = SOURCE.watch(1_000_000)
        upsert_watch(conn, watch, clock.now())
        conn.execute("UPDATE watches SET notes='probe' WHERE watch_id=?", (watch.watch_id,))
        archive = Archive(state)
        client = FetchClient(conn, config, clock, archive, transport=httpx.MockTransport(handler))
        try:
            first = client.fetch(watch.watch_id, get_page_kind(watch.parser), run)
            assert first.snapshot_id is not None
            parse_snapshot(
                database, archive, WorkUnit("parse", "snapshot", first.snapshot_id), clock, run
            )
            clock.current = initial + timedelta(days=7)
            started = clock.now().isoformat()
            conn.executemany(
                "INSERT INTO cursors(name,value) VALUES (?,?)",
                (
                    ("registry_probe_cursor", "1000000"),
                    ("registry_probe_misses", "0"),
                    ("registry_probe_started_at", started),
                ),
            )
            clock.sleep(1)
            second = client.fetch(watch.watch_id, get_page_kind(watch.parser), run)
            assert second.snapshot_id is not None
            parse_snapshot(
                database, archive, WorkUnit("parse", "snapshot", second.snapshot_id), clock, run
            )
            observation = conn.execute(
                "SELECT o.snapshot_id,s.fetched_at FROM observations o "
                "JOIN snapshots s USING(snapshot_id) "
                "WHERE o.scope_kind='dancer' AND o.scope_id='1000000'"
            ).fetchone()
            pointer = conn.execute(
                "SELECT current_observation_snapshot_id FROM watches WHERE watch_id=?",
                (watch.watch_id,),
            ).fetchone()[0]
            with database.transaction():
                advance_sweep(database, clock.now())
            cursor = conn.execute(
                "SELECT value FROM cursors WHERE name='registry_probe_cursor'"
            ).fetchone()
            cursor_value = cursor[0] if cursor else None
            return {
                "probe_started_at": started,
                "first_snapshot": first.snapshot_id,
                "second_snapshot": second.snapshot_id,
                "second_body_changed": second.changed,
                "observation_snapshot": observation[0],
                "observation_fetched_at": observation[1],
                "watch_current_observation_snapshot_id": pointer,
                "probe_cursor_after_advance": cursor_value,
                "fresh_recheck_not_consumed": cursor_value == "1000000",
            }
        finally:
            client.close()


def judge_check() -> dict[str, object]:
    subject = Subject("judge", "synthetic-judge", "Example Judge", event_year=2026)
    dancer = DancerRecord(1, "Example Judge", "leader", 2026, is_pro=True)
    candidates = generate_candidates(subject, [dancer], {})
    assert len(candidates) == 1
    score = score_candidate(candidates[0])
    return {
        "description": "Exact name, eligible pro dancer, fresh activity, unknown judge role",
        "score": score,
        "probable_threshold_at_audit": 0.9,
        "below_publication_threshold_at_audit": score < 0.9,
    }


def trickle_check(state: Path) -> dict[str, object]:
    fixture = (
        Path(__file__).resolve().parents[3]
        / "src/swingset/sources/wsdc_registry/fixtures/lookup-1.body"
    ).read_bytes()
    clock = FakeClock(datetime(2025, 1, 1, tzinfo=UTC))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=fixture)

    config = Config({"points.worldsdc.com": HostConfig()}, {"wsdc_registry": SourceConfig(True)})
    with open_database(state, lock=False) as database:
        conn = database.connection
        run = database.start_run(clock.now(), dry_run=True)
        watch = SOURCE.watch(1)
        upsert_watch(conn, watch, clock.now())
        archive = Archive(state)
        client = FetchClient(conn, config, clock, archive, transport=httpx.MockTransport(handler))
        try:
            first = client.fetch(watch.watch_id, get_page_kind(watch.parser), run)
            assert first.snapshot_id is not None
            parse_snapshot(
                database, archive, WorkUnit("parse", "snapshot", first.snapshot_id), clock, run
            )
            bundle = InputBundle("synthetic", state, {}, config)
            process_unit(database, WorkUnit("project", "dancer", "1"), bundle, clock, run)
            old = conn.execute(
                "SELECT registry_fetched_at FROM dancers WHERE wsdc_id=1"
            ).fetchone()[0]
            clock.current = datetime(2026, 1, 2, tzinfo=UTC)
            conn.execute(
                "INSERT INTO cursors(name,value) VALUES ('registry_probe_next_at',?)",
                ((clock.now() + timedelta(days=30)).isoformat(),),
            )
            discover_registry(database, clock.now())
            second = client.fetch(watch.watch_id, get_page_kind(watch.parser), run)
            if second.snapshot_id is not None:
                parse_snapshot(
                    database, archive, WorkUnit("parse", "snapshot", second.snapshot_id), clock, run
                )
                process_unit(database, WorkUnit("project", "dancer", "1"), bundle, clock, run)
            count = conn.execute("SELECT count(*) FROM snapshots").fetchone()[0]
            after = conn.execute(
                "SELECT registry_fetched_at FROM dancers WHERE wsdc_id=1"
            ).fetchone()[0]
            clock.current += timedelta(days=1)
            discover_registry(database, clock.now())
            row = conn.execute(
                "SELECT notes,next_check_at FROM watches WHERE watch_id=?", (watch.watch_id,)
            ).fetchone()
            return {
                "initial_registry_fetched_at": old,
                "unchanged_fetch_changed": second.changed,
                "snapshot_count_after_unchanged_fetch": count,
                "registry_fetched_at_after": after,
                "next_day_watch": list(row),
                "selected_again_next_day": row[1] == clock.now().isoformat(),
            }
        finally:
            client.close()


def main() -> None:
    with TemporaryDirectory(prefix="swingset-self-healing-") as directory:
        root = Path(directory)
        result = {
            "registry_probe": probe_check(root / "probe"),
            "registry_trickle": trickle_check(root / "trickle"),
            "judge": judge_check(),
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
