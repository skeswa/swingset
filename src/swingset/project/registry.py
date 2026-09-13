"""Registry dancer and placement projection."""

import sqlite3
from datetime import datetime
from typing import TypedDict

from swingset.model.canonical import Dancer, RegistryPlacement
from swingset.model.ids import series_id
from swingset.model.observations import decode_payload
from swingset.normalize.names import normalize_name
from swingset.sources.records import DancerLookup
from swingset.state.findings import Finding

from .writer import Projection


class ProvenanceValues(TypedDict):
    source: str
    snapshot_id: str
    parser_version: str
    first_seen_at: str
    last_seen_at: str
    run_id: str


_LEVELS = {
    "N/A": "none",
    "NEW": "newcomer",
    "NOV": "novice",
    "INT": "intermediate",
    "ADV": "advanced",
    "ALS": "allstar",
    "CHMP": "champion",
    "INV": "invitational",
}
_LEVELS.update(
    {
        value.upper(): value
        for value in (
            "newcomer",
            "novice",
            "intermediate",
            "advanced",
            "allstar",
            "champion",
            "open",
            "invitational",
            "none",
        )
    }
)
_LEVELS.update({"ALL STAR": "allstar", "CHAMPIONS": "champion", "ADVANCE": "advanced"})
_PLACEMENT_DIVISIONS = {
    **_LEVELS,
    "JRS": "juniors",
    "JR": "juniors",
    "JUNIORS": "juniors",
    "SPH": "sophisticated",
    "SOPH": "sophisticated",
    "SOPHISTICATED": "sophisticated",
    "MSTR": "masters",
    "MASTERS": "masters",
    # These source codes are retained literally because their meaning has not
    # been verified. They must not be normalized to a points division.
    "PRO": "PRO",
    "TCH": "TCH",
}
_ROLES = {
    "l": "leader",
    "leader": "leader",
    "primary role leader": "leader",
    "follower": "follower",
    "primary role follower": "follower",
    "f": "follower",
}
_STYLES = {
    "west coast swing": "wcs",
    "wcs": "wcs",
    "country": "country",
    "lindy": "lindy",
    "other": "other",
}


def project_dancer(conn: sqlite3.Connection, scope_id: str, now: str, run_id: str) -> Projection:
    evidence_rows = conn.execute(
        """SELECT o.kind,o.payload_json,o.snapshot_id,o.parser_version,w.source,COALESCE(s.observed_at,s.fetched_at)
        FROM observations o JOIN watches w USING(watch_id) JOIN snapshots s USING(snapshot_id)
        WHERE o.scope_kind='dancer' AND o.scope_id=? ORDER BY COALESCE(s.observed_at,s.fetched_at) DESC,s.snapshot_id DESC""",
        (scope_id,),
    ).fetchall()
    found = []
    for evidence_row in evidence_rows:
        observation = decode_payload(str(evidence_row[0]), str(evidence_row[1]))
        if (
            isinstance(observation, DancerLookup)
            and observation.outcome == "found"
            and observation.wsdc_id is not None
        ):
            found.append((observation, evidence_row))
    if not found:
        return Projection()
    # A later lookup absence never retracts a found identity. Under enforced
    # admission each accepted registry snapshot remains available here.
    payload, row = found[0]
    assert payload.wsdc_id is not None
    first, last = payload.first_name or "", payload.last_name or ""
    provenance: ProvenanceValues = {
        "source": str(row[4]),
        "snapshot_id": str(row[2]),
        "parser_version": str(row[3]),
        "first_seen_at": now,
        "last_seen_at": now,
        "run_id": run_id,
    }

    findings: list[Finding] = []
    unknown_values: set[tuple[str, str]] = set()

    def unknown(field: str, raw: str) -> None:
        if (field, raw) in unknown_values:
            return
        unknown_values.add((field, raw))
        findings.append(
            Finding(
                kind="unknown_enum",
                subject_kind="dancer",
                subject_id=f"{payload.wsdc_id}:{field}:{raw}",
                severity="warning",
                summary=f"Unknown registry {field} value {raw!r}",
                evidence={"snapshot_id": str(row[2]), "field": field, "raw": raw},
                snapshot_id=str(row[2]),
            )
        )

    def level(raw: str | None) -> str:
        if raw is None or raw.strip().casefold() == "none":
            return "none"
        if normalized := _LEVELS.get(raw.strip().upper()):
            return normalized
        unknown("division", raw)
        return "unknown"

    def role(raw: str | None) -> str:
        value = (raw or "unknown").strip().casefold()
        if normalized := _ROLES.get(value):
            return normalized
        unknown("role", raw or "")
        return "unknown"

    def style(raw: str | None) -> str:
        value = (raw or "").strip().casefold()
        if normalized := _STYLES.get(value):
            return normalized
        unknown("dance_style", raw or "")
        return "other"

    def placement_division(raw: str) -> str | None:
        if normalized := _PLACEMENT_DIVISIONS.get(raw.strip().upper()):
            return normalized
        unknown("division", raw)
        return None

    rows: list[Dancer | RegistryPlacement] = [
        Dancer(
            wsdc_id=payload.wsdc_id,
            first_name=first,
            last_name=last,
            name_norm=normalize_name(f"{first} {last}").value,
            is_pro=payload.is_pro,
            primary_role=role(payload.primary_role_raw),
            leader_required_level=level(payload.leader_required_raw),
            leader_allowed_level=level(payload.leader_allowed_raw),
            follower_required_level=level(payload.follower_required_raw),
            follower_allowed_level=level(payload.follower_allowed_raw),
            leader_highest_level=level(payload.leader_highest_raw),
            leader_highest_points=payload.leader_highest_points or 0,
            follower_highest_level=level(payload.follower_highest_raw),
            follower_highest_points=payload.follower_highest_points or 0,
            recent_year=payload.recent_year or 0,
            registry_internal_id=payload.registry_internal_id or 0,
            registry_fetched_at=str(row[5]),
            **provenance,
        )
    ]
    placement_rows: dict[tuple[object, ...], list[RegistryPlacement]] = {}
    for placement, claim_row in (
        (placement, evidence) for lookup, evidence in found for placement in lookup.placements
    ):
        division = placement_division(placement.division_raw)
        if division is None:
            continue
        try:
            month = (
                datetime.strptime(placement.event_month_raw, "%B %Y")
                .date()
                .replace(day=1)
                .isoformat()
            )
        except ValueError:
            month = placement.event_month_raw
        claim_provenance: ProvenanceValues = {
            **provenance,
            "snapshot_id": str(claim_row[2]),
            "parser_version": str(claim_row[3]),
        }
        canonical = RegistryPlacement(
            wsdc_id=payload.wsdc_id,
            role=role(placement.role_raw),
            dance_style=style(placement.dance_style_raw),
            division=division,
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
            **claim_provenance,
        )
        prior_claims = placement_rows.setdefault(canonical.key(), [])
        # Present corrections supersede an older claim with the same native
        # key; omission has no authority. Conflicts within one snapshot remain
        # conflicts and are never settled by row order.
        if not prior_claims or prior_claims[0].snapshot_id == canonical.snapshot_id:
            prior_claims.append(canonical)
    for key, candidates in placement_rows.items():
        claims = {(item.result, item.points) for item in candidates}
        if len(claims) == 1:
            rows.append(candidates[0])
            continue
        findings.append(
            Finding(
                kind="conflict",
                subject_kind="registry_placement",
                subject_id="|".join(str(value) for value in key),
                severity="warning",
                summary="Registry contains conflicting placement claims for one key",
                evidence={
                    "claims": [
                        {"result": result, "points": points} for result, points in sorted(claims)
                    ],
                    "row_count": len(candidates),
                    "snapshot_id": str(row[2]),
                },
                snapshot_id=str(row[2]),
            )
        )
    return Projection(tuple(rows), tuple(findings))
