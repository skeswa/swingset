"""Registry lookup accounting; lookup absence never grants removal authority."""

from typing import Any

from swingset.sources.base import ParseResult
from swingset.sources.records import DancerLookup

from .accounting import account_keys
from .report import Field, Guard


def registry_accounting(
    extract: Any, result: ParseResult, fields: list[Field], guards: list[Guard]
) -> tuple[int | None, int]:
    payload = next(
        (o.payload for o in result.observations if isinstance(o.payload, DancerLookup)), None
    )
    if not isinstance(extract, dict) or payload is None:
        return None, 0
    if payload.outcome == "not_found":
        from swingset.sources.wsdc_registry.adapter import VERIFIED_MISS_SHA256

        exact = extract == {"type": "verified_not_found", "evidence_sha256": VERIFIED_MISS_SHA256}
        guards.append(
            Guard(
                "registry_absence_witness",
                exact,
                "Only the verified miss envelope supports lookup absence",
            )
        )
        fields.append(
            Field("lookup_absence", "handled", "Dated lookup absence; no entity or result removal")
        )
        return 1, 1
    excluded = {
        "dominate_data",
        "non_dominate_data",
        "non_dominate_lookup",
        "dancer_first",
        "dancer_last",
        "dancer_wsdcid",
        "non_dominate_role",
        "dominate_required",
        "dominate_allowed",
        "show_secondary_icons",
        "short_dominate_role",
        "short_non_dominate_role",
        "points_message",
        "show_chmp_warning",
    }
    account_keys(
        {key: value for key, value in extract.items() if key not in excluded},
        {
            "leader",
            "follower",
            "dominate_role",
            "is_pro",
            "recent_year",
            "dominate_role_highest_level",
            "non_dominate_role_highest_level",
            "dominate_role_highest_level_points",
            "non_dominate_role_highest_level_points",
        },
        "$",
        fields,
    )
    fields.extend(
        Field(
            "$." + key,
            "excluded",
            "Duplicated role/display projection; the role envelopes are the source of record",
        )
        for key in sorted(excluded & extract.keys())
    )
    count = 0
    for role in ("leader", "follower"):
        envelope = extract.get(role)
        if not isinstance(envelope, dict):
            fields.append(Field(role, "unknown", "Role envelope is required"))
            continue
        account_keys(
            envelope, {"type", "dancer", "placements", "level", "recent_year"}, role, fields
        )
        account_keys(
            envelope.get("dancer"),
            {"id", "wscid", "first_name", "last_name"},
            role + ".dancer",
            fields,
        )
        levels = envelope.get("level")
        if levels is not None:
            account_keys(
                {key: value for key, value in levels.items() if key != "reason"}
                if isinstance(levels, dict)
                else levels,
                {"required", "allowed"},
                role + ".level",
                fields,
            )
            if isinstance(levels, dict) and "reason" in levels:
                fields.append(
                    Field(
                        role + ".level.reason",
                        "excluded",
                        "Eligibility explanation text; required and allowed source levels are retained",
                    )
                )
        styles = envelope.get("placements")
        if styles == []:
            fields.append(
                Field(role + ".placements", "handled", "Verified empty role placement list")
            )
            continue
        if not isinstance(styles, dict):
            fields.append(Field(role + ".placements", "unknown", "Placements must be an object"))
            continue
        for style, divisions in styles.items():
            guards.append(
                Guard(
                    "registry_style_unknown",
                    str(style).casefold()
                    in {"west coast swing", "wcs", "country", "lindy", "other"},
                    "Registry dance style must have an explicit vocabulary mapping",
                )
            )
            if not isinstance(divisions, dict):
                fields.append(Field(f"{role}.{style}", "unknown", "Division map is required"))
                continue
            for division, detail in divisions.items():
                path = f"{role}.{style}.{division}"
                guards.append(
                    Guard(
                        "registry_division_unknown",
                        str(division).upper()
                        in {
                            "N/A",
                            "NEW",
                            "NOV",
                            "INT",
                            "ADV",
                            "ALS",
                            "CHMP",
                            "INV",
                            "JRS",
                            "JR",
                            "SPH",
                            "SOPH",
                            "MSTR",
                            "PRO",
                            "TCH",
                            "NEWCOMER",
                            "NOVICE",
                            "INTERMEDIATE",
                            "ADVANCED",
                            "ALLSTAR",
                            "CHAMPION",
                            "OPEN",
                            "INVITATIONAL",
                            "JUNIORS",
                            "SOPHISTICATED",
                            "MASTERS",
                        },
                        "Unknown divisions cannot silently disappear from projection",
                    )
                )
                ignored = {"division", "total_points", "wscid", "adv_sliding", "as_sliding"}
                if isinstance(detail, dict):
                    fields.extend(
                        Field(
                            path + "." + key,
                            "excluded",
                            "Division metadata and eligibility summaries; individual competition claims are retained",
                        )
                        for key in sorted(ignored & detail.keys())
                    )
                account_keys(
                    {key: value for key, value in detail.items() if key not in ignored}
                    if isinstance(detail, dict)
                    else detail,
                    {"competitions"},
                    path,
                    fields,
                )
                rows = detail.get("competitions") if isinstance(detail, dict) else None
                if not isinstance(rows, list):
                    fields.append(Field(path, "unknown", "Competition rows must be a list"))
                    continue
                count += len(rows)
                for index, row in enumerate(rows):
                    account_keys(
                        row, {"event", "result", "points", "role"}, f"{path}[{index}]", fields
                    )
                    event = row.get("event") if isinstance(row, dict) else None
                    account_keys(
                        {
                            key: value
                            for key, value in event.items()
                            if key not in {"location", "url"}
                        }
                        if isinstance(event, dict)
                        else event,
                        {"id", "name", "date"},
                        f"{path}[{index}].event",
                        fields,
                    )
                    if isinstance(event, dict):
                        fields.extend(
                            Field(
                                path + "[*].event." + key,
                                "excluded",
                                "Event contact metadata is outside the placement claim",
                            )
                            for key in ("location", "url")
                            if key in event
                        )
                    guards.append(
                        Guard(
                            "required_date_missing",
                            bool(isinstance(event, dict) and event.get("date")),
                            "Registry placement requires its event month",
                        )
                    )
    guards.append(
        Guard(
            "registry_identity_missing",
            payload.outcome == "found"
            and bool(payload.wsdc_id)
            and bool(payload.first_name)
            and bool(payload.last_name),
            "Found lookup requires a complete source identity",
        )
    )
    return count, len(payload.placements)
