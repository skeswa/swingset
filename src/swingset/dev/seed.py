"""Small offline development databases built through the real pipeline."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from swingset.clock import FakeClock
from swingset.fetch.archive import canonical
from swingset.state.environment import ensure_dev

from . import services

VERSION = 1
NOW = datetime(2026, 1, 1, tzinfo=UTC)
COHORT_REF = "eepro:dev-cohort"
COHORT_URL = "https://eepro.com/results/dev-cohort/"
EVENT_ID = "2026-09-example-swing"
ROUND_BODY = b"<table><tr><td>Novice Jack and Jill Finals</td></tr><tr><th>Place</th><th>Leader</th><th>Follower</th></tr><tr><td>1</td><td>Alice Example</td><td>Bob Example</td></tr></table>"
CALENDAR_BODY = b'<html><body><table><tr class="event-trial"><td>Sep 4 - 7, 2026</td><td><div class="event_name"><a href="https://example.test/event">Example Swing</a></div><div class="event_type">Trial Event</div></td><td>Denver, CO, United States</td><td><a href="?country=USA">flag</a></td></tr></table></body></html>'
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "sources"


def fixtures() -> dict[str, bytes]:
    """The reviewed synthetic 33-page cohort plus a captured public dancer."""
    names = [f"round-{number:02}.htm" for number in range(33)]
    index = (
        "<table>"
        + "".join(
            f'<tr><td><a href="{name}">{name}</a></td><td>2026-01-01</td><td>1K</td><td></td></tr>'
            for name in names
        )
        + "</table>"
    ).encode()
    return {
        "https://worldsdc.com/events/": CALENDAR_BODY,
        COHORT_URL: index,
        **{COHORT_URL + name: ROUND_BODY for name in names},
        "https://points.worldsdc.com/lookup2020/find": (
            SOURCE_ROOT / "wsdc_registry" / "fixtures" / "lookup-1.body"
        ).read_bytes(),
    }


def fixture_checksum() -> str:
    return hashlib.sha256(
        canonical(
            {
                "version": VERSION,
                "files": {
                    name: hashlib.sha256(body).hexdigest()
                    for name, body in sorted(fixtures().items())
                },
            }
        )
    ).hexdigest()


def transform_checksum() -> str:
    from swingset.versions import versions

    # A source edit replays on a copy; only explicit transform version changes
    # invalidate the completed seed cache.
    selected = {
        key: value
        for key, value in versions().items()
        if key in {"projector", "linker"} or key.startswith(("extract/", "parser/"))
    }
    return hashlib.sha256(canonical(selected)).hexdigest()


def database_name(name: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", name) or name.startswith("replay_"):
        raise ValueError("Development database names use lowercase letters, digits and underscores")
    return "dev_" + name


def directory(name: str) -> Path:
    return services.home() / "dev" / database_name(name)


def _config(state: Path) -> tuple[Path, Path]:
    config, overrides = state / "config", state / "overrides"
    config.mkdir()
    overrides.mkdir()
    (config / "hosts.toml").write_text(
        "\n".join(
            f'[hosts."{host}"]\nmin_gap_seconds=2\ndaily_request_budget=10000\n'
            for host in ("worldsdc.com", "eepro.com", "points.worldsdc.com")
        )
    )
    (config / "sources.toml").write_text(
        "history_start=2010-01-01\n[sources.wsdc_calendar]\nenabled=true\nindex_urls=[]\n[sources.eepro]\nenabled=true\nindex_urls=[]\n[sources.wsdc_registry]\nenabled=true\nindex_urls=[]\n[scheduling]\nrelease_lag_intervals=0\n[stages]\nevent_pressure=false\nevent_progress_refresh=false\nevent_blocker_observations=false\n"
    )
    # Only fixture decisions enter a seed; no production override is copied.
    (overrides / "event_aliases.csv").write_text(
        f"source,source_ref,event_id,note\neepro,{COHORT_REF},{EVENT_ID},Offline development fixture\n"
    )
    (overrides / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    return config, overrides


def _review(database: Any, generation: str, clock: FakeClock, run_id: str) -> None:
    from swingset.admission.policy import activate_contract, record_review
    from swingset.admission.select import admit_generation
    from swingset.fetch.archive import Archive

    ensure_dev(database.connection)
    with database.transaction() as conn:
        row = conn.execute(
            "SELECT page_kind,report_json FROM source_generations WHERE generation_id=%s",
            (generation,),
        ).fetchone()
        if row is None or json.loads(row[1])["failures"]:
            raise RuntimeError("Development fixture failed its interpretation contract")
        receipt = record_review(
            conn,
            (generation,),
            reviewer="offline-development-fixture",
            reviewed_at=clock.now().isoformat(),
            evidence="Known offline fixture cohort " + fixture_checksum(),
        )
        activate_contract(conn, str(row[0]), receipt)
    selected = admit_generation(
        database,
        Archive(database.state_dir),
        generation,
        now=clock.now().isoformat(),
        run_id=run_id,
    )
    if selected != "accepted":
        raise RuntimeError(f"Development fixture was not admitted: {selected}")


def populate(state: Path, *, seconds: float = 30.0) -> dict[str, Any]:
    """Fetch mock bodies, parse/admit them, then run bounded real offline work."""
    import httpx

    from swingset.fetch.archive import Archive
    from swingset.fetch.client import FetchClient
    from swingset.schedule.derive import derive_one
    from swingset.schedule.parse import parse_snapshot
    from swingset.schedule.watches import upsert_watch
    from swingset.sources import get_page_kind
    from swingset.sources.base import WatchSpec
    from swingset.sources.wsdc_registry.adapter import SOURCE as REGISTRY
    from swingset.state import derivations
    from swingset.state.db import open_database
    from swingset.state.inputs import accept, capture
    from swingset.state.work import WorkUnit, iter_work
    from swingset.versions import versions

    started = time.monotonic()
    clock = FakeClock(NOW)
    bodies = fixtures()
    config, overrides = _config(state)
    wire: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        wire.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        body = bodies.get(str(request.url))
        if body is None:
            raise RuntimeError("Seed attempted a request outside its bounded fixture corpus")
        return httpx.Response(200, content=body)

    with open_database(state) as database:
        ensure_dev(database.connection)
        bundle = capture(config, overrides, state, versions())
        accept(database, bundle, clock)
        run_id = database.start_run(clock.now())
        archive = Archive(state)
        specs = [
            WatchSpec(
                "",
                "wsdc_calendar",
                "calendar",
                "GET",
                "https://worldsdc.com/events/",
                "wsdc_calendar.events",
            ),
            WatchSpec(
                "",
                "eepro",
                "autoindex",
                "GET",
                COHORT_URL,
                "eepro.autoindex",
                source_ref=COHORT_REF,
            ),
            *[
                WatchSpec(
                    "",
                    "eepro",
                    "round",
                    "GET",
                    COHORT_URL + f"round-{number:02}.htm",
                    "eepro.round",
                    source_ref=COHORT_REF,
                )
                for number in range(33)
            ],
            REGISTRY.watch(1),
        ]
        reviewed: set[str] = set()
        client = FetchClient(
            database.connection,
            bundle.config,
            clock,
            archive,
            transport=httpx.MockTransport(handler),
            random_value=lambda: 0,
        )
        try:
            for spec in specs:
                if time.monotonic() - started > seconds:
                    raise RuntimeError("Development seed exceeded its wall-clock bound")
                upsert_watch(database.connection, spec, clock.now())
                fetched = client.fetch(spec.watch_id, get_page_kind(spec.parser), run_id)
                if fetched.snapshot_id is None:
                    raise RuntimeError(f"Development fixture was not fetched: {fetched.skipped}")
                parsed = parse_snapshot(
                    database,
                    archive,
                    WorkUnit("parse", "snapshot", fetched.snapshot_id),
                    clock,
                    run_id,
                )
                if parsed.failed:
                    raise RuntimeError(f"Development fixture parse failed: {spec.parser}")
                if spec.parser not in reviewed:
                    row = database.connection.execute(
                        "SELECT generation_id FROM source_generations WHERE unit_key=(SELECT unit_key FROM source_units WHERE watch_id=%s LIMIT 1) ORDER BY created_at DESC,generation_id DESC LIMIT 1",
                        (spec.watch_id,),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("Development parse did not retain a source generation")
                    _review(database, str(row[0]), clock, run_id)
                    reviewed.add(spec.parser)
            completed = 0
            while True:
                hints: dict[WorkUnit, str] = {}
                unit = None
                for stage in ("parse", "project", "link"):
                    unit = next(
                        iter_work(database.connection, stage, now=clock.now(), current_hints=hints),
                        None,
                    )
                    if unit is not None:
                        break
                derivations.drop_current_hints(database, hints)
                if unit is None:
                    break
                if completed >= 256 or time.monotonic() - started > seconds:
                    raise RuntimeError("Development seed derivation exceeded its bound")
                outcome = derive_one(database, archive, unit, bundle, clock, run_id)
                if outcome.failed:
                    from swingset.state import queue

                    failure = queue.get(database.connection, unit)
                    detail = failure.last_error if failure is not None else outcome.reason
                    raise RuntimeError(
                        f"Development seed work {unit.stage}/{unit.unit_kind}/{unit.unit_id} failed: {detail}"
                    )
                completed += 1
                clock.sleep(1)
            counts = {
                table: int(
                    database.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for table in (
                    "snapshots",
                    "source_generations",
                    "observations",
                    "events",
                    "dancers",
                    "derivation_scopes",
                    "pending_work",
                )
            }
            if counts["pending_work"]:
                pending = [
                    tuple(row)
                    for row in database.connection.execute(
                        "SELECT stage,unit_kind,unit_id FROM pending_work ORDER BY stage,unit_kind,unit_id LIMIT 10"
                    )
                ]
                raise RuntimeError(f"Development seed did not settle its pending work: {pending}")
            database.connection.execute(
                "UPDATE runs SET finished_at=%s,summary_json=%s WHERE run_id=%s",
                (clock.now().isoformat(), json.dumps({"seed": counts}), run_id),
            )
        finally:
            client.close()
    return {
        "counts": counts,
        "wire_requests": len(wire),
        "derived_units": completed,
        "seconds": round(time.monotonic() - started, 4),
        "fixture_checksum": fixture_checksum(),
    }


def create(
    name: str = "default",
    *,
    service: services.Service | None = None,
    state: Path | None = None,
    reuse: bool = False,
    scenario: str = "event-projection",
) -> dict[str, Any]:
    from psycopg import sql

    from swingset.state import postgres_schema
    from swingset.state.engine import write_settings

    from .scenarios import NAMES, prepare

    if scenario not in NAMES:
        raise ValueError("Unknown development scenario")

    service = service or services.ensure_service("dev")
    name_in_database = database_name(name)
    state = state or directory(name)
    receipt_path = state / "seed.json"
    expected = {
        "fixture_checksum": fixture_checksum(),
        "migration_checksum": postgres_schema.migration_checksum(),
        "roles_checksum": postgres_schema.roles_checksum(),
        "transform_checksum": transform_checksum(),
        "scenario": scenario,
    }
    with service.connect() as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (name_in_database,)
        ).fetchone()
        if exists:
            if not reuse or not receipt_path.is_file():
                raise ValueError("Development seed already exists; use a new name")
            receipt = services._json(receipt_path)
            if not receipt.get("complete") or any(
                receipt.get(key) != value for key, value in expected.items()
            ):
                raise ValueError(
                    "Seed schema, fixtures or transform versions changed; create a new seed name"
                )
            with service.connect(name_in_database) as source:
                stored = ensure_dev(source)
                if receipt.get("database_id") != stored.database_id:
                    raise ValueError("Development seed identity differs from its receipt")
            return receipt
        if state.exists():
            raise ValueError("Development files directory already exists; use a new name")
        admin.execute(
            sql.SQL(
                "CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"
            ).format(sql.Identifier(name_in_database))
        )
    state.mkdir(parents=True)
    receipt = {
        **expected,
        "format": "swingset-development-seed-v1",
        "name": name,
        "complete": False,
    }
    services.write_private(receipt_path, receipt)
    with service.connect(name_in_database) as conn:
        postgres_schema.migrate(conn, environment="dev")
        receipt["database_id"] = ensure_dev(conn).database_id
    write_settings(
        state,
        service.dsn(name_in_database, role="worker"),
        environment="dev",
        control_dsn=service.dsn(name_in_database, role="control"),
    )
    services.write_private(receipt_path, receipt)
    receipt["summary"] = populate(state)
    if scenario != "event-projection":
        receipt["scenario_result"] = prepare(state, scenario)
    receipt["complete"] = True
    services.write_private(receipt_path, receipt)
    return receipt


def ensure_default() -> dict[str, Any]:
    return create(reuse=True)


def drop(
    name: str, *, service: services.Service | None = None, state: Path | None = None
) -> dict[str, Any]:
    """Drop only a named local dev database whose private receipt still matches."""
    from psycopg import sql

    service = service or services.ensure_service("dev")
    database = database_name(name)
    state = state or directory(name)
    receipt_path = state / "seed.json"
    with service.connect() as admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone()
        if not exists and not state.exists():
            return {"name": name, "removed": False}
        if not receipt_path.is_file():
            raise ValueError("Development seed ownership receipt is missing")
        receipt = services._json(receipt_path)
        if receipt.get("name") != name or receipt.get("format") != "swingset-development-seed-v1":
            raise ValueError("Development seed ownership receipt differs")
        if exists:
            with service.connect(database) as conn:
                stored = ensure_dev(conn)
                if stored.database_id != receipt.get("database_id"):
                    raise ValueError("Development seed identity differs from its receipt")
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
    shutil.rmtree(state)
    return {"name": name, "removed": True}
