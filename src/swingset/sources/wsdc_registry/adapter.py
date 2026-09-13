"""WSDC registry lookup adapter. Unknown miss envelopes remain invalid."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import cast

from ..base import (
    ExtractError,
    JsonValue,
    Observation,
    ObservationScope,
    ParseContext,
    ParseResult,
    ParseWarning,
    WatchSpec,
)
from ..interpretation import declared
from ..records import DancerLookup, RegistryPlacement

VERIFIED_MISS_SHA256 = "8437bd0ef46a19c9a7c294c53e0429b40e76ebbd5fe9fd73a9025752495ddb1c"


def is_verified_miss(body: bytes, status: int) -> bool:
    """Match only the twice-observed 2026-09-08 absent-id response."""
    return status == 404 and hashlib.sha256(body).hexdigest() == VERIFIED_MISS_SHA256


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


class DancerPage:
    kind = "wsdc_registry.dancer"
    EXTRACT_VERSION = 2
    PARSER_VERSION = 3
    change_mode = "body_hash"

    def extract(self, body: bytes) -> JsonValue:
        if hashlib.sha256(body).hexdigest() == VERIFIED_MISS_SHA256:
            return {"type": "verified_not_found", "evidence_sha256": VERIFIED_MISS_SHA256}
        try:
            value: JsonValue = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExtractError("registry response is not JSON") from exc
        if not isinstance(value, dict):
            raise ExtractError("registry response is not an object")
        return value

    @declared
    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        if not isinstance(extract, dict):
            raise ExtractError("registry extract is not an object")
        requested = _integer(
            dict(ctx_form(ctx)).get("num") or (ctx.source_ref or "").removeprefix("wsdc:")
        )
        if (
            extract.get("type") == "verified_not_found"
            and extract.get("evidence_sha256") == VERIFIED_MISS_SHA256
        ):
            payload = DancerLookup("dancer_lookup", "not_found", requested)
            return ParseResult(
                (Observation(ObservationScope("dancer", str(requested)), payload.kind, payload),)
            )
        role_envelopes = [extract.get("leader"), extract.get("follower")]
        envelopes = [
            item
            for item in role_envelopes
            if isinstance(item, dict) and item.get("type") == "dancer"
        ]
        envelope = envelopes[0] if envelopes else extract
        dancer = envelope.get("dancer") if isinstance(envelope, dict) else None
        if not isinstance(dancer, dict):
            payload = DancerLookup("dancer_lookup", "invalid", requested)
            warning = ParseWarning(
                "invalid_response", "Registry miss shape has not been verified", extract
            )
            return ParseResult(
                (Observation(ObservationScope("dancer", str(requested)), payload.kind, payload),),
                warnings=(warning,),
            )
        wsdc_id = _integer(dancer.get("wscid"))
        if wsdc_id != requested:
            payload = DancerLookup("dancer_lookup", "invalid", requested)
            warning = ParseWarning(
                "invalid_response",
                "Registry returned a different WSDC id",
                {"requested": requested, "returned": wsdc_id},
            )
            return ParseResult(
                (Observation(ObservationScope("dancer", str(requested)), payload.kind, payload),),
                warnings=(warning,),
            )
        placements: list[RegistryPlacement] = []
        for role_name in ("leader", "follower"):
            role = extract.get(role_name)
            styles = role.get("placements", {}) if isinstance(role, dict) else {}
            if not isinstance(styles, dict):
                continue
            for style_name, divisions in styles.items():
                if not isinstance(divisions, dict):
                    continue
                for division_code, division in divisions.items():
                    competitions = (
                        division.get("competitions", []) if isinstance(division, dict) else []
                    )
                    if not isinstance(competitions, list):
                        continue
                    for row in competitions:
                        event = row.get("event", {}) if isinstance(row, dict) else {}
                        if not isinstance(row, dict) or not isinstance(event, dict):
                            continue
                        placements.append(
                            RegistryPlacement(
                                role_name,
                                str(division_code),
                                str(event.get("id")) if event.get("id") is not None else None,
                                str(event.get("name", "")),
                                str(event.get("date", "")),
                                str(row.get("result", "")),
                                _integer(row.get("points")),
                                str(style_name),
                            )
                        )
        leader = (
            cast(dict[str, JsonValue], extract.get("leader"))
            if isinstance(extract.get("leader"), dict)
            else {}
        )
        follower = (
            cast(dict[str, JsonValue], extract.get("follower"))
            if isinstance(extract.get("follower"), dict)
            else {}
        )
        leader_level = (
            cast(dict[str, JsonValue], leader.get("level"))
            if isinstance(leader.get("level"), dict)
            else {}
        )
        follower_level = (
            cast(dict[str, JsonValue], follower.get("level"))
            if isinstance(follower.get("level"), dict)
            else {}
        )
        primary_role = str(extract.get("dominate_role", "unknown"))
        primary_is_leader = "leader" in primary_role.casefold()
        primary_highest = extract.get("dominate_role_highest_level")
        secondary_highest = extract.get("non_dominate_role_highest_level")
        primary_points = _integer(extract.get("dominate_role_highest_level_points"))
        secondary_points = _integer(extract.get("non_dominate_role_highest_level_points"))
        payload = DancerLookup(
            kind="dancer_lookup",
            outcome="found",
            requested_wsdc_id=requested,
            wsdc_id=wsdc_id,
            registry_internal_id=_integer(dancer.get("id")),
            first_name=str(dancer.get("first_name", "")),
            last_name=str(dancer.get("last_name", "")),
            primary_role_raw=primary_role,
            is_pro=bool(extract.get("is_pro", 0)),
            leader_required_raw=str(leader_level.get("required"))
            if leader_level.get("required") is not None
            else None,
            leader_allowed_raw=str(leader_level.get("allowed"))
            if leader_level.get("allowed") is not None
            else None,
            follower_required_raw=str(follower_level.get("required"))
            if follower_level.get("required") is not None
            else None,
            follower_allowed_raw=str(follower_level.get("allowed"))
            if follower_level.get("allowed") is not None
            else None,
            recent_year=_integer(extract.get("recent_year")),
            placements=tuple(placements),
            leader_highest_raw=str(primary_highest if primary_is_leader else secondary_highest)
            if (primary_highest if primary_is_leader else secondary_highest) is not None
            else None,
            leader_highest_points=primary_points if primary_is_leader else secondary_points,
            follower_highest_raw=str(secondary_highest if primary_is_leader else primary_highest)
            if (secondary_highest if primary_is_leader else primary_highest) is not None
            else None,
            follower_highest_points=secondary_points if primary_is_leader else primary_points,
        )
        return ParseResult(
            (Observation(ObservationScope("dancer", str(requested)), payload.kind, payload),)
        )

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()


def ctx_form(ctx: ParseContext) -> tuple[tuple[str, str], ...]:
    # The requested id is normally the source_ref. Kept separate for future
    # contexts that expose canonical form data.
    return (("num", (ctx.source_ref or "").removeprefix("wsdc:")),)


@dataclass(frozen=True)
class RegistrySource:
    name: str = "wsdc_registry"
    hosts: frozenset[str] = frozenset({"points.worldsdc.com"})
    page_kinds = {DancerPage.kind: DancerPage()}

    def watch(self, wsdc_id: int) -> WatchSpec:
        return WatchSpec(
            f"registry-{wsdc_id}",
            self.name,
            "dancer",
            "POST",
            "https://points.worldsdc.com/lookup2020/find",
            DancerPage.kind,
            (("num", str(wsdc_id)),),
            f"wsdc:{wsdc_id}",
        )

    def seed_watches(self, config: object, overrides: object) -> list[WatchSpec]:
        return []


SOURCE = RegistrySource()
