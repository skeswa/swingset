"""Bounded schema-37 SQLite samples for the one-time Postgres import rehearsal.

The production connection opens mode=ro. Only TEMP markers and a fresh scratch
checkpoint are written; the source transaction and legacy state shared lock
bound a consistent, file-complete sample without copying the whole database.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import shutil
import sqlite3
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from swingset.fetch.archive import canonical, durable_write

from . import history_catalog as history
from . import postgres_schema as schema
from .legacy_checkpoint import verify_sqlite_files


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _marker(table: str) -> str:
    return "temp." + _quote("_sample_" + table)


def _join(left: str, right: str, columns: tuple[str, ...]) -> str:
    return " AND ".join(f"{left}.{_quote(name)}={right}.{_quote(name)}" for name in columns)


@contextlib.contextmanager
def _shared_source(state: Path) -> Iterator[None]:
    """Honor an existing SQLite worker's lock files; never create source files."""
    opened = []
    try:
        for name in ("state.intent", "state.lock"):
            path = state / name
            if path.exists():
                handle = path.open("rb")
                opened.append(handle)
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        if len(opened) == 2:
            fcntl.flock(opened[0].fileno(), fcntl.LOCK_UN)
        yield
    finally:
        for handle in reversed(opened):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sample(
    source: Path,
    target: Path,
    *,
    unit_limit: int = 2500,
    event_ids: tuple[str, ...] = (),
    include_link_history: bool = False,
    include_derivation_history: bool = True,
    max_database_bytes: int = 1 << 30,
    max_file_bytes: int = 2 << 30,
    timeout_seconds: float = 540,
) -> dict[str, Any]:
    """Measure the complete reference/file closure before materializing a sample."""
    source = source.resolve(strict=True)
    target = target.resolve()
    if (
        target.exists()
        or target.is_relative_to(source.parent)
        or unit_limit < 0
        or (unit_limit == 0 and not event_ids)
        or min(max_database_bytes, max_file_bytes) < 1
    ):
        raise ValueError(
            "sampling requires positive budgets and a fresh target outside source state"
        )
    started = time.monotonic()
    deadline = started + timeout_seconds
    work = target.with_name("." + target.name + ".sampling-" + uuid.uuid4().hex)
    work.mkdir(parents=True)
    tables = schema.reference_manifest()["tables"]
    counts: dict[str, int] = {}
    estimated = 0
    visited_json_bytes = 0
    selected_units: list[str] = []

    def expired() -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError("SQLite sampling exceeded its bounded reader deadline")

    try:
        with (
            _shared_source(source.parent),
            contextlib.closing(
                sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
            ) as conn,
        ):
            conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
            conn.execute("BEGIN")
            if conn.execute("PRAGMA user_version").fetchone()[0] != 37:
                raise ValueError("sampler requires frozen SQLite schema 37")
            catalog = conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
            expected_catalog = [
                (item["type"], item["name"], item["tbl_name"], item["sql"])
                for item in schema.reference_manifest()["objects"]
            ]
            if catalog != expected_catalog:
                raise ValueError("source schema differs from the frozen schema-37 reference")
            for table in tables:
                names = ",".join(_quote(name) for name in history.keys(table))
                declared = {column["name"]: column["type"] for column in tables[table]["columns"]}
                definition = ",".join(
                    _quote(name) + " " + declared[name] + " NOT NULL"
                    for name in history.keys(table)
                )
                conn.execute(
                    f"CREATE TEMP TABLE {_quote('_sample_' + table)}({definition},expanded INTEGER NOT NULL DEFAULT 0,PRIMARY KEY({names}))"
                )
            conn.execute("CREATE TEMP TABLE _sample_tokens(token TEXT PRIMARY KEY)")

            def add(table: str, query: str, values: tuple[Any, ...] = ()) -> int:
                names = ",".join(_quote(name) for name in history.keys(table))
                return max(
                    0,
                    conn.execute(
                        f"INSERT OR IGNORE INTO {_marker(table)}({names}) {query}", values
                    ).rowcount,
                )

            for table in (
                "meta",
                "revisions",
                "blob_classes",
                "accepted_inputs",
                "derivation_input_versions",
                "identity_journal_acceptances",
                "identity_decisions",
                "control_state",
                "control_events",
                "operator_pauses",
                "admission_policies",
                "hosts",
                "host_budget",
                "host_request_spacing",
                "host_request_spacing_baselines",
                "series",
            ):
                names = ",".join(_quote(name) for name in history.keys(table))
                add(table, f"SELECT {names} FROM {_quote(table)}")
            if event_ids:
                for event_id in dict.fromkeys(event_ids):
                    if not add(
                        "events", "SELECT event_id FROM events WHERE event_id=?", (event_id,)
                    ):
                        raise ValueError("explicit sample event is absent")
                    if include_derivation_history:
                        add(
                            "derivation_scopes",
                            "SELECT stage,unit_kind,unit_id FROM derivation_scopes WHERE (stage='project' OR (? AND stage='link')) AND unit_kind='event' AND unit_id=?",
                            (include_link_history, event_id),
                        )
                    add(
                        "canonical_scope_rows",
                        "SELECT scope_kind,scope_id,table_name,record_key FROM canonical_scope_rows WHERE scope_kind='event' AND scope_id=?",
                        (event_id,),
                    )
                    add(
                        "source_event_map",
                        "SELECT source,source_ref FROM source_event_map WHERE event_id=?",
                        (event_id,),
                    )
            else:
                add("events", "SELECT event_id FROM events")
            groups = conn.execute(
                "SELECT DISTINCT w.source,w.kind FROM source_units u JOIN watches w USING(watch_id) WHERE u.accepted_generation_id IS NOT NULL ORDER BY w.source,w.kind"
            ).fetchall()
            # Equal source/kind quotas keep small sources in the corpus. The
            # remaining capacity samples the large registry cohort by key.
            quota = max(1, unit_limit // max(1, len(groups)))
            for source_name, kind in groups:
                selected_units.extend(
                    row[0]
                    for row in conn.execute(
                        "SELECT u.unit_key FROM source_units u JOIN watches w USING(watch_id) WHERE u.accepted_generation_id IS NOT NULL AND w.source=? AND w.kind=? ORDER BY u.unit_key LIMIT ?",
                        (source_name, kind, quota),
                    )
                )
            if len(selected_units) < unit_limit:
                existing = set(selected_units)
                for (key,) in conn.execute(
                    "SELECT unit_key FROM source_units WHERE accepted_generation_id IS NOT NULL ORDER BY unit_key"
                ):
                    if key not in existing:
                        selected_units.append(key)
                        existing.add(key)
                        if len(selected_units) == unit_limit:
                            break
            selected_units = selected_units[:unit_limit]
            conn.executemany(
                f"INSERT OR IGNORE INTO {_marker('source_units')}(unit_key) VALUES(?)",
                ((key,) for key in selected_units),
            )
            if include_derivation_history:
                add(
                    "derivation_scopes",
                    f"SELECT d.stage,d.unit_kind,d.unit_id FROM derivation_scopes d JOIN watches w ON w.source_ref='wsdc:'||d.unit_id JOIN {_marker('source_units')} u ON u.unit_key=w.watch_id WHERE d.stage='project' AND d.unit_kind='dancer'",
                )
            add(
                "admission_reviews",
                "SELECT report_digest FROM admission_reviews WHERE report_digest IN (SELECT reviewed_report_digest FROM admission_policies)",
            )
            edges: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
                *history._OWNERSHIP,
                *history._SOFT,
            ]
            for table, specification in tables.items():
                if table != "snapshots" and any(
                    column["name"] == "snapshot_id" for column in specification["columns"]
                ):
                    edges.append((table, "snapshots", ("snapshot_id",), ("snapshot_id",)))
                grouped: dict[int, list[tuple[Any, ...]]] = {}
                for foreign in conn.execute(f"PRAGMA foreign_key_list({_quote(table)})"):
                    grouped.setdefault(foreign[0], []).append(foreign)
                for items in grouped.values():
                    items.sort(key=lambda item: item[1])
                    edges.append(
                        (
                            table,
                            items[0][2],
                            tuple(item[3] for item in items),
                            tuple(item[4] for item in items),
                        )
                    )
            edges += [
                ("watches", "snapshots", ("watch_id",), ("watch_id",)),
                ("watches", "source_units", ("watch_id",), ("watch_id",)),
                ("source_units", "source_generations", ("unit_key",), ("unit_key",)),
                ("watches", "registry_verifications", ("watch_id",), ("watch_id",)),
                ("watches", "findings", ("watch_id",), ("watch_id",)),
                ("findings", "finding_support", ("finding_id",), ("finding_id",)),
                ("findings", "finding_support_references", ("finding_id",), ("finding_id",)),
            ]
            edges += [
                ("watches", "observations", ("watch_id",), ("watch_id",)),
                (
                    "derivation_generations",
                    "derivation_scopes",
                    ("stage", "unit_kind", "unit_id"),
                    ("stage", "unit_kind", "unit_id"),
                ),
                (
                    "derivation_scopes",
                    "canonical_scope_rows",
                    ("unit_kind", "unit_id"),
                    ("scope_kind", "scope_id"),
                ),
                ("events", "contests", ("event_id",), ("event_id",)),
                ("events", "entries", ("event_id",), ("event_id",)),
                ("events", "placements", ("event_id",), ("event_id",)),
                ("events", "judges", ("event_id",), ("event_id",)),
                ("contests", "rounds", ("contest_id",), ("contest_id",)),
                ("rounds", "heats", ("round_id",), ("round_id",)),
                ("rounds", "callback_marks", ("round_id",), ("round_id",)),
                ("rounds", "callbacks", ("round_id",), ("round_id",)),
                ("rounds", "final_marks", ("round_id",), ("round_id",)),
                ("entries", "identity_links", ("entry_id",), ("subject_id",)),
                ("judges", "identity_links", ("judge_id",), ("subject_id",)),
                ("identity_links", "dancers", ("wsdc_id",), ("wsdc_id",)),
            ]
            while True:
                expired()
                added = 0
                for table in tables:
                    key_columns = history.keys(table)
                    relation = " AND ".join(
                        "t." + _quote(name) + "=json_extract(o.record_key,'$[" + str(index) + "]')"
                        for index, name in enumerate(key_columns)
                    )
                    selected = ",".join("t." + _quote(name) for name in key_columns)
                    added += add(
                        table,
                        f"SELECT {selected} FROM {_marker('canonical_scope_rows')} k CROSS JOIN canonical_scope_rows o ON {_join('o', 'k', history.keys('canonical_scope_rows'))} CROSS JOIN {_quote(table)} t ON {relation} WHERE k.table_name=?",
                        (table,),
                    )
                if include_derivation_history:
                    added += add(
                        "derivation_scopes",
                        f"SELECT d.stage,d.unit_kind,d.unit_id FROM {_marker('observations')} k CROSS JOIN observations o ON o.observation_id=k.observation_id JOIN derivation_scopes d ON d.stage='project' AND d.unit_kind=o.scope_kind AND d.unit_id=o.scope_id",
                    )
                added += add(
                    "pending_work",
                    f"SELECT p.stage,p.unit_kind,p.unit_id FROM {_marker('snapshots')} k CROSS JOIN pending_work p ON p.unit_id=k.snapshot_id WHERE p.stage='parse' AND p.unit_kind='snapshot'",
                )
                for left, right, local, remote in edges:
                    names = ",".join("t." + _quote(name) for name in history.keys(right))
                    relation = " AND ".join(
                        f"s.{_quote(a)}=t.{_quote(b)}" for a, b in zip(local, remote, strict=True)
                    )
                    added += add(
                        right,
                        f"SELECT DISTINCT {names} FROM {_marker(left)} k CROSS JOIN {_quote(left)} s ON {_join('s', 'k', history.keys(left))} JOIN {_quote(right)} t ON {relation}",
                    )
                for table, specification in tables.items():
                    columns = [
                        column["name"]
                        for column in specification["columns"]
                        if column["name"].endswith("_json") or column["name"] == "record_key"
                    ]
                    if not columns:
                        continue
                    key_names = ",".join("s." + _quote(name) for name in history.keys(table))
                    for key in conn.execute(
                        f"SELECT {key_names} FROM {_marker(table)} k CROSS JOIN {_quote(table)} s ON {_join('s', 'k', history.keys(table))} WHERE NOT k.expanded"
                    ).fetchall():
                        condition = " AND ".join(
                            _quote(name) + "=?" for name in history.keys(table)
                        )
                        for column in columns:

                            def blocks(
                                table: str = table,
                                column: str = column,
                                condition: str = condition,
                                key: tuple[Any, ...] = key,
                            ) -> Iterator[bytes]:
                                nonlocal visited_json_bytes
                                # substr(CAST(large_text AS BLOB)) re-reads the
                                # entire overflow value for every chunk. The
                                # read-only incremental blob handle does not.
                                rowid, stored = conn.execute(
                                    f"SELECT rowid,typeof({_quote(column)}) FROM {_quote(table)} WHERE {condition}",
                                    key,
                                ).fetchone()
                                if stored == "null":
                                    return
                                with conn.blobopen(table, column, rowid, readonly=True) as blob:
                                    visited_json_bytes += len(blob)
                                    if visited_json_bytes > max_database_bytes:
                                        raise ValueError(
                                            "sample JSON reference closure exceeds the database budget"
                                        )
                                    yield from iter(lambda: blob.read(65536), b"")

                            for tokens in history._token_blocks(blocks()):
                                conn.executemany(
                                    "INSERT OR IGNORE INTO _sample_tokens(token) VALUES(?)",
                                    ((token,) for token in tokens),
                                )
                    conn.execute(f"UPDATE {_marker(table)} SET expanded=1 WHERE NOT expanded")
                for table, column in history.JSON_ID_TARGETS:
                    added += add(
                        table,
                        f"SELECT t.{_quote(column)} FROM _sample_tokens token CROSS JOIN {_quote(table)} t ON token.token=t.{_quote(column)}",
                    )
                for pack_table, kind in (
                    ("source_generation_packs", "parse_body"),
                    ("label_body_packs", "label_body"),
                ):
                    added += add(
                        "objects",
                        f"SELECT o.class,o.digest FROM objects o JOIN {_quote(pack_table)} p ON p.pack_digest=o.digest JOIN {_marker(pack_table)} k USING(generation_id) WHERE o.class='{kind}'",
                    )
                added += add(
                    "objects",
                    f"SELECT o.class,o.digest FROM objects o JOIN derivation_generations g ON g.output_digest=o.digest JOIN {_marker('derivation_evicted')} k USING(generation_id) WHERE o.class='derivation_output'",
                )
                if added == 0:
                    break
            for table, specification in tables.items():
                size_expr = "+".join(
                    f"coalesce(length(CAST(s.{_quote(column['name'])} AS BLOB)),0)+8"
                    for column in specification["columns"]
                )
                count, size = conn.execute(
                    f"SELECT count(*),coalesce(sum({size_expr}),0) FROM {_marker(table)} k CROSS JOIN {_quote(table)} s ON {_join('s', 'k', history.keys(table))}"
                ).fetchone()
                counts[table] = count
                estimated += size
                if estimated > max_database_bytes:
                    raise ValueError("complete sample rows exceed the database budget")
            expired()
            path = work / "state.sqlite"
            with contextlib.closing(sqlite3.connect(path)) as destination:
                destination.execute("PRAGMA journal_mode=DELETE")
                for kind, _name, _table, statement in catalog:
                    if kind == "table":
                        destination.execute(statement)
                for table, specification in tables.items():
                    names = ",".join(_quote(column["name"]) for column in specification["columns"])
                    physical = "rowid," if table in schema.ROWID_TABLES else ""
                    selected = ",".join(
                        "s." + _quote(column["name"]) for column in specification["columns"]
                    )
                    if physical:
                        selected = "s.rowid," + selected
                    placeholders = ",".join(
                        "?" for _ in range(len(specification["columns"]) + bool(physical))
                    )
                    cursor = conn.execute(
                        f"SELECT {selected} FROM {_marker(table)} k CROSS JOIN {_quote(table)} s ON {_join('s', 'k', history.keys(table))}"
                    )
                    destination.executemany(
                        f"INSERT INTO {_quote(table)}({physical}{names}) VALUES({placeholders})",
                        cursor,
                    )
                    destination.commit()
                    expired()
                    if path.stat().st_size > max_database_bytes:
                        raise ValueError("materialized SQLite sample exceeds its database budget")
                if conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"
                ).fetchone():
                    destination.execute("DELETE FROM sqlite_sequence")
                    destination.executemany(
                        "INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)",
                        conn.execute("SELECT name,seq FROM sqlite_sequence"),
                    )
                for kind, _name, _table, statement in catalog:
                    if kind != "table":
                        destination.execute(statement)
                destination.execute("PRAGMA user_version=37")
                destination.commit()
                if destination.execute(
                    "PRAGMA foreign_key_check"
                ).fetchone() or destination.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise ValueError("sample closure fails SQLite integrity or foreign keys")
                files = verify_sqlite_files(
                    source.parent,
                    destination,
                    include_paths=True,
                    max_file_bytes=max_file_bytes,
                    include_publication=False,
                )
            for name in files.pop("paths"):
                expired()
                destination_file = work / name
                destination_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source.parent / name, destination_file)
            conn.rollback()
        entries = {
            file.relative_to(work).as_posix(): {
                "size": file.stat().st_size,
                "sha256": _digest(file),
            }
            for file in work.rglob("*")
            if file.is_file()
        }
        receipt = {
            "format": "swingset-sqlite37-sample-v1",
            "schema_version": 37,
            "unit_limit": unit_limit,
            "event_ids": list(event_ids),
            "include_link_history": include_link_history,
            "include_derivation_history": include_derivation_history,
            "visited_json_bytes": visited_json_bytes,
            "selected_units": len(selected_units),
            "unit_keys_sha256": hashlib.sha256(canonical(selected_units)).hexdigest(),
            "rows": counts,
            "estimated_database_bytes": estimated,
            "database_bytes": path.stat().st_size,
            "required_files": files,
            "files": entries,
            "seconds": round(time.monotonic() - started, 3),
            "source_read_only": True,
            "complete": True,
        }
        durable_write(work / "checkpoint.json", canonical(receipt))
        os.replace(work, target)
        return receipt
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
