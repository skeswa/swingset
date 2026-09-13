"""Stable attempt inputs for work isolation, without H15 materialization state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from .work import WorkUnit


def input_fingerprint(
    conn: sqlite3.Connection, unit: WorkUnit, *, recipe_inputs: Mapping[str, str] | None = None
) -> str:
    """Hash source inputs, never queue timestamps or attempts' own findings.

    Link candidates depend on the whole registry, so its semantic revision is
    relevant there. Other global revisions are deliberately excluded. This is
    retry identity only, not a materialized-generation or publication contract.
    """
    if unit.stage in {"project", "link"}:
        from .derivations import available, desired

        if available(conn):
            return desired(conn, unit, recipe=recipe_inputs).fingerprint
    result = hashlib.sha256()

    def add(value: object) -> None:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        result.update(len(raw).to_bytes(8, "big"))
        result.update(raw)

    def query(sql: str, values: tuple[Any, ...] = ()) -> None:
        add(sql)
        for row in conn.execute(sql, values):
            add(tuple(row))

    add(("work-attempt-v1", unit.stage, unit.unit_kind, unit.unit_id))
    selected = {"version/repository", "recipe/runtime"}
    if unit.stage == "parse":
        row = conn.execute(
            "SELECT s.body_sha256,s.body_bytes,s.url,s.via,s.archive_url,s.captured_at,w.parser,w.source_ref,w.source,w.url,w.archive_url "
            "FROM snapshots s JOIN watches w USING(watch_id) WHERE snapshot_id=?",
            (unit.unit_id,),
        ).fetchone()
        add(tuple(row) if row else None)
        if row:
            from swingset.sources import get_page_kind

            try:
                page = get_page_kind(str(row[6]))
            except KeyError:
                add(("unavailable_parser", row[6]))
            else:
                add((page.EXTRACT_VERSION, page.PARSER_VERSION))
            selected.update({f"version/extract/{row[6]}", f"version/parser/{row[6]}"})
            query("SELECT * FROM admission_policies WHERE page_kind=?", (row[6],))
    elif unit.stage == "project":
        selected.update(
            {
                "version/projector",
                "config/sources.toml",
                "overrides/event_aliases.csv",
                "overrides/source_urls.csv",
                "overrides/series_aliases.csv",
            }
        )
        if unit.unit_kind in {"map", "history"}:
            query(
                "SELECT observation_id,payload_json FROM observations WHERE scope_kind IN ('calendar','source_index') ORDER BY observation_id"
            )
            query("SELECT * FROM source_events ORDER BY source,source_ref")
            query("SELECT * FROM source_event_map ORDER BY source,source_ref")
            if unit.unit_kind == "history":
                query(
                    "SELECT * FROM registry_placements ORDER BY wsdc_id,role,series_id,event_month,division,dance_style"
                )
                query("SELECT * FROM events ORDER BY event_id")
        elif unit.unit_kind == "event":
            query(
                "SELECT o.observation_id,o.payload_json FROM observations o WHERE (o.scope_kind='event' AND o.scope_id=?) OR (o.scope_kind='source_event' AND EXISTS (SELECT 1 FROM source_event_map m WHERE m.source_ref=o.scope_id AND m.event_id=?)) ORDER BY o.observation_id",
                (unit.unit_id, unit.unit_id),
            )
        else:
            query(
                "SELECT observation_id,payload_json FROM observations WHERE scope_kind=? AND scope_id=? ORDER BY observation_id",
                (unit.unit_kind, unit.unit_id),
            )
            if unit.unit_kind == "source_event":
                query(
                    "SELECT * FROM source_event_map WHERE source_ref=? ORDER BY source",
                    (unit.unit_id,),
                )
    elif unit.stage == "link":
        selected.update(
            {
                "version/linker",
                "link/weights.toml",
                "overrides/nicknames.csv",
                "overrides/identity_overrides.csv",
            }
        )
        for table in ("events", "entries", "judges", "contests", "placements"):
            # Every table has a unique first column and an indexed event key.
            query(f"SELECT * FROM {table} WHERE event_id=? ORDER BY 1", (unit.unit_id,))
        query("SELECT value FROM revisions WHERE name='dancers'")
        query(
            "SELECT * FROM registry_placements WHERE event_id=? ORDER BY wsdc_id,role,series_id,event_month,division,dance_style",
            (unit.unit_id,),
        )
        query("SELECT key,value FROM meta WHERE key='identity_journal_digest'")
    if recipe_inputs is not None:
        add(dict(recipe_inputs))
    else:
        for name, value in conn.execute(
            "SELECT input_name,digest FROM accepted_inputs WHERE consumer='pipeline' ORDER BY input_name"
        ):
            if name in selected:
                add((name, value))
    return result.hexdigest()
