"""Registry dancer and placement projection."""

import sqlite3
from datetime import datetime
from typing import TypedDict

from swingset.model.canonical import Dancer, RegistryPlacement
from swingset.model.ids import series_id
from swingset.model.observations import decode_payload
from swingset.normalize.divisions import classify_contest
from swingset.normalize.names import normalize_name
from swingset.sources.records import DancerLookup

from .writer import Projection


class ProvenanceValues(TypedDict):
    source: str
    snapshot_id: str
    parser_version: str
    first_seen_at: str
    last_seen_at: str
    run_id: str


def project_dancer(conn: sqlite3.Connection, scope_id: str, now: str, run_id: str) -> Projection:
    row = conn.execute(
        """SELECT o.kind,o.payload_json,o.snapshot_id,o.parser_version,w.source,s.fetched_at
        FROM observations o JOIN watches w USING(watch_id) JOIN snapshots s USING(snapshot_id)
        WHERE o.scope_kind='dancer' AND o.scope_id=? ORDER BY s.fetched_at DESC,s.snapshot_id DESC LIMIT 1""",
        (scope_id,),
    ).fetchone()
    if row is None:
        return Projection()
    payload = decode_payload(str(row[0]), str(row[1]))
    if (
        not isinstance(payload, DancerLookup)
        or payload.outcome != "found"
        or payload.wsdc_id is None
    ):
        return Projection()
    first, last = payload.first_name or "", payload.last_name or ""
    provenance: ProvenanceValues = {
        "source": str(row[4]),
        "snapshot_id": str(row[2]),
        "parser_version": str(row[3]),
        "first_seen_at": now,
        "last_seen_at": now,
        "run_id": run_id,
    }

    def level(raw: str | None) -> str:
        return classify_contest(raw or "").division

    role_raw = (payload.primary_role_raw or "unknown").casefold()
    role = (
        "leader"
        if role_raw in {"l", "leader"}
        else "follower"
        if role_raw in {"f", "follower"}
        else "unknown"
    )
    rows: list[Dancer | RegistryPlacement] = [
        Dancer(
            wsdc_id=payload.wsdc_id,
            first_name=first,
            last_name=last,
            name_norm=normalize_name(f"{first} {last}").value,
            is_pro=payload.is_pro,
            primary_role=role,
            leader_required_level=level(payload.leader_required_raw),
            leader_allowed_level=level(payload.leader_allowed_raw),
            follower_required_level=level(payload.follower_required_raw),
            follower_allowed_level=level(payload.follower_allowed_raw),
            leader_highest_level="none",
            leader_highest_points=0,
            follower_highest_level="none",
            follower_highest_points=0,
            recent_year=payload.recent_year or 0,
            registry_internal_id=payload.registry_internal_id or 0,
            registry_fetched_at=str(row[5]),
            **provenance,
        )
    ]
    for placement in payload.placements:
        try:
            month = (
                datetime.strptime(placement.event_month_raw, "%B %Y")
                .date()
                .replace(day=1)
                .isoformat()
            )
        except ValueError:
            month = placement.event_month_raw
        rows.append(
            RegistryPlacement(
                wsdc_id=payload.wsdc_id,
                role=placement.role_raw.casefold(),
                dance_style=(placement.dance_style_raw or "wcs").casefold(),
                division=level(placement.division_raw),
                series_id=series_id(
                    placement.event_name_raw,
                    int(placement.event_id_raw)
                    if placement.event_id_raw and placement.event_id_raw.isdigit()
                    else None,
                ),
                series_name_raw=placement.event_name_raw,
                event_month=month,
                event_id=None,
                result=placement.result_raw,
                points=placement.points,
                **provenance,
            )
        )
    return Projection(tuple(rows))
