"""Run new code over a bounded development slice on an independent scratch copy."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from swingset.clock import FakeClock
from swingset.state.environment import ensure_dev
from swingset.state.work import WorkUnit

from . import seed, services

MAX_UNITS = 128
MAX_FILES = 4096
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_FILES_BYTES = 256 * 1024 * 1024


def source_state(database: str | Path) -> Path:
    path = Path(database).expanduser()
    if path.is_dir():
        return path.resolve()
    name = str(database).removeprefix("dev_")
    return seed.directory(name).resolve()


def _files_bounded(state: Path) -> int:
    count, total = 0, 0
    for path in state.rglob("*"):
        if path.is_symlink():
            if path.readlink().is_absolute() or not path.resolve().is_relative_to(state.resolve()):
                raise ValueError("Replay requires contained relative file links")
        if path.is_file() and path.name != "database.json":
            size = path.stat().st_size
            count += 1
            total += size
            if size > MAX_FILE_BYTES or count > MAX_FILES or total > MAX_FILES_BYTES:
                raise ValueError("Development replay files exceed the bounded slice limit")
    return total


def _source_database(state: Path, service: services.Service) -> str:
    from psycopg.conninfo import conninfo_to_dict

    from swingset.state.engine import read_settings

    settings = read_settings(state)
    info = conninfo_to_dict(settings["dsn"])
    name = str(info.get("dbname", ""))
    if (
        not name.startswith("dev_")
        or name.startswith("dev_replay_")
        or info.get("host") != service.host
        or int(str(info.get("port", "0"))) != service.port
    ):
        raise ValueError("Replay accepts only this local service's named development databases")
    with service.connect(name) as conn:
        ensure_dev(conn)
    return name


def _ownership_path(identifier: str) -> Path:
    return services.home() / "replays" / f"{identifier}.json"


def _drop_owned(service: services.Service, record: dict[str, Any]) -> None:
    from psycopg import sql

    if not re.fullmatch(r"dev_replay_[0-9a-f]{24}", record["database"]):
        raise RuntimeError("Replay ownership metadata names an invalid scratch database")
    with service.connect() as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (record["database"],)
        ).fetchone()
        if exists:
            with service.connect(record["database"]) as scratch:
                stored = ensure_dev(scratch)
                if stored.database_id not in {
                    record["source_identity"],
                    record["scratch_identity"],
                }:
                    raise RuntimeError(
                        "Refusing to remove a scratch database with another identity"
                    )
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(record["database"]))
            )


def clean_dead_replays(service: services.Service) -> int:
    cleaned = 0
    for path in (services.home() / "replays").glob("*.json"):
        record = services._json(path)
        if record["service_kind"] != service.kind or services.run_alive(record):
            continue
        _drop_owned(service, record)
        scratch = Path(record["files"])
        if scratch.resolve().parent != Path("/tmp").resolve() or not scratch.name.startswith(
            "swingset-replay-"
        ):
            raise RuntimeError("Replay ownership metadata names an invalid files directory")
        shutil.rmtree(scratch, ignore_errors=True)
        path.unlink()
        cleaned += 1
    return cleaned


@contextmanager
def clone(state: Path, service: services.Service) -> Iterator[Path]:
    """Postgres clones only a quiescent source; it never terminates source users."""
    from psycopg import sql

    from swingset.state import postgres_schema
    from swingset.state.engine import write_settings

    _files_bounded(state)
    source = _source_database(state, service)
    clean_dead_replays(service)
    identifier = uuid.uuid4().hex[:24]
    name = "dev_replay_" + identifier
    with service.connect(source) as conn:
        source_identity = ensure_dev(conn).database_id
    with tempfile.TemporaryDirectory(prefix="swingset-replay-", dir="/tmp") as temporary:
        files = Path(temporary) / "state"
        record: dict[str, Any] = {
            "pid": os.getpid(),
            "process_group": os.getpgrp(),
            "database": name,
            "source_identity": source_identity,
            "scratch_identity": str(uuid.uuid4()),
            "service_kind": service.kind,
            "files": temporary,
        }
        ownership = _ownership_path(identifier)
        services.write_private(ownership, record)
        try:
            with service.connect() as admin:
                admin.execute("SELECT set_config('statement_timeout','5000',false)")
                admin.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                        sql.Identifier(name), sql.Identifier(source)
                    )
                )
            with service.connect(name) as scratch:
                ensure_dev(scratch)
                postgres_schema.seed_environment(
                    scratch, environment="dev", database_id=record["scratch_identity"]
                )
            shutil.copytree(
                state,
                files,
                symlinks=True,
                ignore=shutil.ignore_patterns("database.json", "database.json.*.tmp"),
            )
            write_settings(
                files,
                service.dsn(name, role="worker"),
                environment="dev",
                control_dsn=service.dsn(name, role="control"),
            )
            yield files
        finally:
            _drop_owned(service, record)
            ownership.unlink(missing_ok=True)


def _selected(database: Any, unit: WorkUnit) -> tuple[str, int] | None:
    if unit.stage == "parse":
        row = database.connection.execute(
            "SELECT g.result_json FROM source_units u JOIN source_generations g ON g.generation_id=u.accepted_generation_id JOIN snapshots s ON s.watch_id=u.watch_id WHERE s.snapshot_id=%s",
            (unit.unit_id,),
        ).fetchone()
        return None if row is None else (hashlib.sha256(str(row[0]).encode()).hexdigest(), 1)
    from swingset.state.verify import _live_label

    return _live_label(database.connection, unit)


def _parse_again(database: Any, unit: WorkUnit, clock: FakeClock, run_id: str, token: str) -> None:
    from swingset.fetch.archive import Archive
    from swingset.schedule.parse import parse_snapshot
    from swingset.state.work import enqueue

    conn = database.connection
    row = conn.execute(
        "SELECT w.parser FROM snapshots s JOIN watches w ON w.watch_id=s.watch_id WHERE s.snapshot_id=%s",
        (unit.unit_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Replay snapshot is missing")
    # Keep the captured context and snapshot provenance stable. A unique
    # scratch-only recipe forces fresh extract/parse work without reusing an
    # immutable production generation or requiring a declared version bump.
    with database.transaction():
        for scope in ("extract", "parse"):
            name = f"recipe/code/{scope}/{row[0]}"
            nonce = hashlib.sha256((token + name).encode()).hexdigest()
            conn.execute(
                "UPDATE accepted_inputs SET digest=%s WHERE consumer='pipeline' AND input_name=%s",
                (nonce, name),
            )
        enqueue(conn, (unit,), enqueued_at=clock.now().isoformat())
    result = parse_snapshot(database, Archive(database.state_dir), unit, clock, run_id)
    if result.failed or result.selection not in {None, "accepted", "shadow"}:
        raise RuntimeError(
            f"Candidate parser did not produce an admissible fixture result: failed={result.failed}, selection={result.selection}"
        )


def _units(database: Any, unit: str | None, event: str | None, limit: int) -> list[WorkUnit]:
    conn = database.connection
    if unit is not None:
        parts = unit.split("/", 2)
        if len(parts) != 3 or parts[0] not in {"parse", "project", "link"}:
            raise ValueError(
                "Unit keys use parse/snapshot/<id>, project/<kind>/<id> or link/event/<id>"
            )
        selected = WorkUnit(*parts)
        if selected.stage == "parse":
            exists = (
                selected.unit_kind == "snapshot"
                and conn.execute(
                    "SELECT 1 FROM snapshots WHERE snapshot_id=%s", (selected.unit_id,)
                ).fetchone()
                is not None
            )
        else:
            exists = (
                conn.execute(
                    "SELECT 1 FROM derivation_scopes WHERE stage=%s AND unit_kind=%s AND unit_id=%s AND materialized_generation_id IS NOT NULL",
                    (selected.stage, selected.unit_kind, selected.unit_id),
                ).fetchone()
                is not None
            )
        if not exists:
            raise ValueError("Replay unit is absent from this development slice")
        return [selected]
    if (
        event is None
        or conn.execute("SELECT 1 FROM events WHERE event_id=%s", (event,)).fetchone() is None
    ):
        raise ValueError("Replay event is absent from this development slice")
    from swingset.state import derivations
    from swingset.state.derivation_dependencies import prerequisites, waits_for

    known = {
        WorkUnit(str(stage), str(kind), str(identifier))
        for stage, kind, identifier in conn.execute(
            "SELECT stage,unit_kind,unit_id FROM derivation_scopes WHERE stage IN ('project','link') AND materialized_generation_id IS NOT NULL"
        )
    }
    wanted = {
        candidate
        for candidate in (WorkUnit("project", "event", event), WorkUnit("link", "event", event))
        if candidate in known
    }
    if not wanted:
        raise ValueError("Replay event has no materialized transform in this slice")
    discovered: set[WorkUnit] = set()
    snapshots: set[str] = set()
    while wanted - discovered:
        candidate = min(wanted - discovered)
        discovered.add(candidate)
        selection = derivations.desired(conn, candidate)
        dependencies = [
            *selection.dependencies,
            *(item for group in selection.dependency_sets.values() for item in group),
        ]
        upstream = {
            *prerequisites(conn, candidate),
            *waits_for(conn, candidate),
            *(WorkUnit(*item["key"]) for item in dependencies if item["kind"] == "derivation"),
        }
        wanted.update(upstream & known)
        for item in dependencies:
            if item.get("snapshot_id"):
                snapshots.add(str(item["snapshot_id"]))
            if item["kind"] == "source_selection":
                row = conn.execute(
                    "SELECT g.recipe_json FROM source_units u JOIN source_generations g ON g.generation_id=u.accepted_generation_id WHERE u.unit_key=%s",
                    (item["key"],),
                ).fetchone()
                if row is not None:
                    import json

                    snapshots.add(str(json.loads(row[0])["context"]["snapshot_id"]))
        if len(wanted) + len(snapshots) > limit:
            raise ValueError("Event replay exceeds the bounded unit limit; choose one unit instead")
    parsed = [WorkUnit("parse", "snapshot", snapshot) for snapshot in sorted(snapshots)]
    derived = sorted(discovered)
    pending = set(derived)
    order: list[WorkUnit] = []
    while pending:
        ready = []
        for candidate in sorted(pending):
            selection = derivations.desired(conn, candidate)
            dependencies = [
                *selection.dependencies,
                *(item for group in selection.dependency_sets.values() for item in group),
            ]
            upstream = {
                *prerequisites(conn, candidate),
                *waits_for(conn, candidate),
                *(WorkUnit(*item["key"]) for item in dependencies if item["kind"] == "derivation"),
            }
            if not upstream & pending:
                ready.append(candidate)
        if not ready:
            raise RuntimeError("Development replay graph contains a dependency cycle")
        order.extend(ready)
        pending.difference_update(ready)
    return [*parsed, *order]


def run(
    database: str | Path,
    *,
    unit: str | None = None,
    event: str | None = None,
    max_units: int = MAX_UNITS,
    seconds: float = 30.0,
    service: services.Service | None = None,
) -> dict[str, Any]:
    if (
        (unit is None) == (event is None)
        or not 1 <= max_units <= MAX_UNITS
        or not 0 < seconds <= 30
    ):
        raise ValueError("Replay needs one unit or event, at most 128 units and at most 30 seconds")
    from swingset.link import link_event
    from swingset.project.process import process_unit
    from swingset.state import derivations
    from swingset.state.db import open_database
    from swingset.state.inputs import accept, capture
    from swingset.versions import versions

    started = time.monotonic()
    service = service or services.ensure_service("dev")
    state = source_state(database)
    results = []
    with clone(state, service) as scratch, open_database(scratch) as working:
        ensure_dev(working.connection)
        clock = FakeClock(seed.NOW)
        bundle = capture(scratch / "config", scratch / "overrides", scratch, versions())
        accept(working, bundle, clock)
        run_id = working.start_run(clock.now())
        token = uuid.uuid4().hex[:16]
        selected_units = _units(working, unit, event, max_units)
        print(
            "Replay units: "
            + ", ".join(
                "/".join((item.stage, item.unit_kind, item.unit_id)) for item in selected_units
            ),
            file=sys.stderr,
            flush=True,
        )
        before_labels = {selected: _selected(working, selected) for selected in selected_units}
        for selected in selected_units:
            if time.monotonic() - started > seconds:
                raise RuntimeError("Development replay exceeded its wall-clock bound")
            from swingset.state.write_deadline import bounded_write

            with bounded_write(working.connection, seconds=seconds - (time.monotonic() - started)):
                before = before_labels[selected]
                if selected.stage == "parse":
                    _parse_again(working, selected, clock, run_id, token)
                    dependency_digest = None
                else:
                    with working.transaction():
                        selection = derivations.capture(
                            working.connection,
                            selected,
                            now=clock.now(),
                            context={"development_replay": token},
                        )
                    if selected.stage == "project":
                        process_unit(working, selected, bundle, clock, run_id, selection=selection)
                    else:
                        link_event(
                            working, selected.unit_id, bundle, clock, run_id, selection=selection
                        )
                    from swingset.state.verify_graph import _stored_dependency

                    generation = derivations.selected_generation(working.connection, selected)
                    if generation is None:
                        raise RuntimeError(
                            "Development replay did not materialize the selected unit"
                        )
                    dependency_digest = _stored_dependency(working.connection, selected, generation)
                after = _selected(working, selected)
                results.append(
                    {
                        "unit": "/".join((selected.stage, selected.unit_kind, selected.unit_id)),
                        "state": "equal" if before == after else "changed",
                        "before": before,
                        "after": after,
                        "dependencies": dependency_digest,
                    }
                )
            clock.sleep(1)
    return {
        "format": "swingset-development-replay-v1",
        "complete": True,
        "units": len(results),
        "results": results,
        "seconds": round(time.monotonic() - started, 4),
    }
