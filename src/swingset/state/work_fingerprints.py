"""Stable attempt inputs for work isolation, without H15 materialization state."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from swingset.state import engine

from .work import WorkUnit


def input_fingerprint(
    conn: engine.Connection, unit: WorkUnit, *, recipe_inputs: Mapping[str, str] | None = None
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

    def query(sql: str, values: tuple[Any, ...] = (), *, identity: str) -> None:
        # Preserve the opaque v1 retry witness across SQL ports.
        add(identity)
        for row in conn.execute(sql, values):
            add(tuple(row))

    add(("work-attempt-v1", unit.stage, unit.unit_kind, unit.unit_id))
    selected = {"version/repository", "recipe/runtime"}
    if unit.stage == "parse":
        # The page kind's own recipe and labels (D-0207), plus the whole
        # package: a parse unit runs admission, observation and verification
        # code outside its kind's scope, and a deploy that fixes it must
        # re-open the attempts it blocked. This is retry identity only; it
        # re-opens blocked units, never re-queues parsed ones.
        selected = {"version/repository"}
        row = conn.execute(
            (
                "SELECT s.body_sha256, s.body_bytes, s.url, s.via, s.archive_url, s.captured_at, w.parser, "
                "w.source_ref, w.source, w.url AS watch_url, w.archive_url AS watch_archive_url FROM snapshots AS s JOIN watches AS w USING "
                "(watch_id) WHERE snapshot_id = %s"
            ),
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
            from swingset.admission.generations import parse_input_names

            selected.update(parse_input_names(str(row[6])))
            query(
                (
                    "SELECT admission_policies.page_kind, admission_policies.contract_version, "
                    "admission_policies.mode, admission_policies.policy_revision, "
                    "admission_policies.reviewed_report_digest, admission_policies.reviewed_by, "
                    "admission_policies.reviewed_at FROM admission_policies WHERE page_kind = %s"
                ),
                (row[6],),
                identity="SELECT * FROM admission_policies WHERE page_kind=?",
            )
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
                (
                    "SELECT observation_id, payload_json FROM observations WHERE scope_kind IN ('calendar', "
                    "'source_index') ORDER BY observation_id NULLS FIRST"
                ),
                identity="SELECT observation_id,payload_json FROM observations WHERE scope_kind IN ('calendar','source_index') ORDER BY observation_id",
            )
            query(
                (
                    "SELECT source_events.source, source_events.source_ref, source_events.name_raw, "
                    "source_events.start_date, source_events.end_date, source_events.location_raw, "
                    "source_events.url, source_events.snapshot_id, source_events.parser_version, "
                    "source_events.first_seen_at, source_events.last_seen_at, source_events.run_id FROM "
                    "source_events ORDER BY source NULLS FIRST, source_ref NULLS FIRST"
                ),
                identity="SELECT * FROM source_events ORDER BY source,source_ref",
            )
            query(
                (
                    "SELECT source_event_map.source, source_event_map.source_ref, source_event_map.event_id, "
                    "source_event_map.match_method, source_event_map.match_confidence FROM source_event_map "
                    "ORDER BY source NULLS FIRST, source_ref NULLS FIRST"
                ),
                identity="SELECT * FROM source_event_map ORDER BY source,source_ref",
            )
            if unit.unit_kind == "history":
                query(
                    (
                        "SELECT registry_placements.wsdc_id, registry_placements.role, "
                        "registry_placements.dance_style, registry_placements.division, "
                        "registry_placements.series_id, registry_placements.series_name_raw, "
                        "registry_placements.event_month, registry_placements.event_id, registry_placements.result, "
                        "registry_placements.points, registry_placements.source, registry_placements.snapshot_id, "
                        "registry_placements.parser_version, registry_placements.first_seen_at, "
                        "registry_placements.last_seen_at, registry_placements.run_id FROM registry_placements ORDER "
                        "BY wsdc_id NULLS FIRST, role NULLS FIRST, series_id NULLS FIRST, event_month NULLS FIRST, "
                        "division NULLS FIRST, dance_style NULLS FIRST"
                    ),
                    identity="SELECT * FROM registry_placements ORDER BY wsdc_id,role,series_id,event_month,division,dance_style",
                )
                query(
                    (
                        "SELECT events.event_id, events.series_id, events.name, events.year, events.start_date, "
                        "events.end_date, events.city, events.region, events.country, events.website, "
                        "events.wsdc_status, events.sources, events.live_window_start, events.live_window_end, "
                        "events.source, events.snapshot_id, events.parser_version, events.first_seen_at, "
                        "events.last_seen_at, events.run_id, events.event_month, events.date_precision, events.held, "
                        "events.coverage_tier, events.history_source FROM events ORDER BY event_id NULLS FIRST"
                    ),
                    identity="SELECT * FROM events ORDER BY event_id",
                )
        elif unit.unit_kind == "event":
            query(
                (
                    "SELECT o.observation_id, o.payload_json FROM observations AS o WHERE (o.scope_kind = 'event' AND o.scope_id = %s) OR (o.scope_kind = 'source_event' AND EXISTS(SELECT 1 AS _value_0 FROM source_event_map AS m WHERE m.source_ref = o.scope_id AND m.event_id = %s)) ORDER BY o.observation_id NULLS FIRST"
                ),
                (unit.unit_id, unit.unit_id),
                identity="SELECT o.observation_id,o.payload_json FROM observations o WHERE (o.scope_kind='event' AND o.scope_id=?) OR (o.scope_kind='source_event' AND EXISTS (SELECT 1 FROM source_event_map m WHERE m.source_ref=o.scope_id AND m.event_id=?)) ORDER BY o.observation_id",
            )
        else:
            query(
                (
                    "SELECT observation_id, payload_json FROM observations WHERE scope_kind = %s AND scope_id = "
                    "%s ORDER BY observation_id NULLS FIRST"
                ),
                (unit.unit_kind, unit.unit_id),
                identity="SELECT observation_id,payload_json FROM observations WHERE scope_kind=? AND scope_id=? ORDER BY observation_id",
            )
            if unit.unit_kind == "source_event":
                query(
                    (
                        "SELECT source_event_map.source, source_event_map.source_ref, source_event_map.event_id, "
                        "source_event_map.match_method, source_event_map.match_confidence FROM source_event_map "
                        "WHERE source_ref = %s ORDER BY source NULLS FIRST"
                    ),
                    (unit.unit_id,),
                    identity="SELECT * FROM source_event_map WHERE source_ref=? ORDER BY source",
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
            query(
                f"SELECT * FROM {table} WHERE event_id=%s ORDER BY 1",
                (unit.unit_id,),
                identity=f"SELECT * FROM {table} WHERE event_id=? ORDER BY 1",
            )
        query(
            "SELECT value FROM revisions WHERE name = 'dancers'",
            identity="SELECT value FROM revisions WHERE name='dancers'",
        )
        query(
            (
                "SELECT registry_placements.wsdc_id, registry_placements.role, "
                "registry_placements.dance_style, registry_placements.division, "
                "registry_placements.series_id, registry_placements.series_name_raw, "
                "registry_placements.event_month, registry_placements.event_id, registry_placements.result, "
                "registry_placements.points, registry_placements.source, registry_placements.snapshot_id, "
                "registry_placements.parser_version, registry_placements.first_seen_at, "
                "registry_placements.last_seen_at, registry_placements.run_id FROM registry_placements WHERE "
                "event_id = %s ORDER BY wsdc_id NULLS FIRST, role NULLS FIRST, series_id NULLS FIRST, "
                "event_month NULLS FIRST, division NULLS FIRST, dance_style NULLS FIRST"
            ),
            (unit.unit_id,),
            identity="SELECT * FROM registry_placements WHERE event_id=? ORDER BY wsdc_id,role,series_id,event_month,division,dance_style",
        )
        query(
            "SELECT key, value FROM meta WHERE key = 'identity_journal_digest'",
            identity="SELECT key,value FROM meta WHERE key='identity_journal_digest'",
        )
    if recipe_inputs is not None:
        add(dict(recipe_inputs))
    else:
        for name, value in conn.execute(
            "SELECT input_name, digest FROM accepted_inputs WHERE consumer = 'pipeline' ORDER BY "
            "input_name NULLS FIRST"
        ):
            if name in selected:
                add((name, value))
    return result.hexdigest()
