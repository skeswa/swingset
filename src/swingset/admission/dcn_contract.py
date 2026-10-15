"""Fail-closed accounting for the danceconvention.net archive and results pages (D-0255).

This module never imports the adapter: `contracts.py` imports it, and
`contracts.py` is in every recipe scope, so an adapter import here would put
the adapter in every scope too. The few adapter rules it repeats (which rows
are followed, which finals are one table, the bounded warnings) are pinned
equal to the adapter's by `tests/test_dcn_live.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from swingset.sources.base import ParseContext, ParseResult
from swingset.sources.records import RoundSheet, SourceEventRow

from .accounting import account_keys
from .report import Field, Guard

KINDS = frozenset({"dcn.archive", "dcn.event"})

KNOWN_BOUNDED_WARNINGS = frozenset(
    {
        "dcn_nonfinal_rounds_withheld",
        "dcn_role_split_final_withheld",
        "dcn_empty_final_withheld",
        "dcn_names_masked_at_source",
        "dcn_results_unpublished",
    }
)

ROOT = "https://danceconvention.net/eventdirector/en/eventpage/"


@dataclass(frozen=True)
class Accounting:
    fields: tuple[Field, ...]
    guards: tuple[Guard, ...]
    source_count: int | None
    interpreted_count: int
    listed_children: tuple[str, ...] = ()
    interpreted_children: tuple[str, ...] = ()
    terminal: str | None = "captured_document_end"
    removal: str = "none"


def accounting(ctx: ParseContext, extract: Any, result: ParseResult) -> Accounting:
    if ctx.kind == "dcn.archive":
        return _archive(extract, result)
    if ctx.kind == "dcn.event":
        return _event(extract, result)
    raise ValueError(f"unsupported DCN contract kind: {ctx.kind}")


def _unreviewed(extract: Any, fields: list[Field]) -> None:
    names = extract.get("unreviewed_fields") if isinstance(extract, dict) else None
    if not isinstance(names, list):
        fields.append(Field("$.unreviewed_fields", "unknown", "Expected a list of field names"))
        return
    # A field the adapter does not read could change what a row means.
    fields.extend(Field(str(name), "unknown", "Unreviewed source field") for name in names)


def _archive(extract: Any, result: ParseResult) -> Accounting:
    fields: list[Field] = []
    account_keys(extract, {"events", "unreviewed_fields"}, "$", fields)
    _unreviewed(extract, fields)
    events = extract.get("events") if isinstance(extract, dict) else None
    if not isinstance(events, list):
        events = []
    followed = []
    for index, event in enumerate(events):
        account_keys(
            event,
            {"id", "name", "start", "end", "affiliations", "results", "published"},
            f"$.events[{index}]",
            fields,
        )
        if (
            isinstance(event, dict)
            and "WSDC" in (event.get("affiliations") or ())
            and event.get("results") is True
            and event.get("published") is True
        ):
            followed.append(f"{ROOT}{event.get('id')}/results")
    if len(followed) < len(events):
        fields.append(
            Field(
                "$.events[not followed]",
                "excluded",
                "Rows without a WSDC affiliation, a results flag or publication are other "
                "dance styles or have nothing to read",
            )
        )
    rows = [o.payload for o in result.observations if isinstance(o.payload, SourceEventRow)]
    guards = [
        Guard(
            "required_date_missing",
            all(row.date_raw for row in rows),
            "Every followed archive row carries its source dates",
        ),
        Guard(
            "dcn_listing_matches_watches",
            tuple(row.url for row in rows) == tuple(watch.url for watch in result.watches),
            "Each followed row has exactly one results watch, in source order",
        ),
    ]
    return Accounting(
        tuple(fields),
        tuple(guards),
        len(followed),
        len(rows),
        tuple(followed),
        tuple(watch.url for watch in result.watches),
    )


def single_table_final(rankings: Any) -> bool:
    """Whether a final's printed places are exactly 1..N without tie intervals."""
    if not isinstance(rankings, list) or not rankings:
        return False
    ranks = [item.get("rank") if isinstance(item, dict) else None for item in rankings]
    return ranks == list(range(1, len(ranks) + 1)) and not any(
        isinstance(item, dict) and item.get("interval") is not None for item in rankings
    )


def _event(extract: Any, result: ParseResult) -> Accounting:
    fields: list[Field] = []
    account_keys(
        extract, {"event", "publish_results", "contests", "unreviewed_fields"}, "$", fields
    )
    _unreviewed(extract, fields)
    event = extract.get("event") if isinstance(extract, dict) else None
    account_keys(event, {"id", "name", "start", "end", "affiliations"}, "$.event", fields)
    contests = extract.get("contests") if isinstance(extract, dict) else None
    if not isinstance(contests, list):
        contests = []
    published = isinstance(extract, dict) and extract.get("publish_results") is True
    finals: dict[str, int] = {}
    for index, contest in enumerate(contests):
        account_keys(
            contest, {"id", "name", "division_type", "rounds"}, f"$.contests[{index}]", fields
        )
        rounds = contest.get("rounds") if isinstance(contest, dict) else None
        for number, item in enumerate(rounds if isinstance(rounds, list) else []):
            path = f"$.contests[{index}].rounds[{number}]"
            account_keys(
                item,
                {"id", "name", "final", "scores_available", "scores_link", "rankings"},
                path,
                fields,
            )
            if not isinstance(item, dict):
                continue
            rankings = item.get("rankings")
            if not isinstance(rankings, list):
                rankings = []
            for position, ranking in enumerate(rankings):
                account_keys(
                    ranking,
                    {"role", "name", "partner", "rank", "interval"},
                    f"{path}.rankings[{position}]",
                    fields,
                )
            if published and item.get("final") is True and single_table_final(rankings):
                finals[str(item.get("id"))] = len(rankings)
    fields.extend(
        (
            Field(
                "$.contests[*].rounds[*].scores_link",
                "excluded",
                "Score PDFs (bibs, judges, marks) are not fetched (D-0255)",
            ),
            Field(
                "nonfinal_rounds",
                "excluded",
                "Preliminary rankings list only eliminated dancers and wait for the score PDFs",
            ),
            Field(
                "masked_names",
                "excluded",
                "Names the source masks stay empty; no identity is inferred",
            ),
        )
    )
    sheets = [o.payload for o in result.observations if isinstance(o.payload, RoundSheet)]
    interpreted = sum(len(table.rows) for sheet in sheets for table in sheet.tables)
    guards = [
        Guard(
            "dcn_round_sheets_are_finals",
            [sheet.source_round_ref for sheet in sheets] == list(finals),
            "Exactly the published single-table finals become round sheets, in source order",
        ),
        Guard(
            "dcn_published_without_results",
            not published or bool(contests),
            "A published results tab with no contests is held for review, never admitted",
        ),
        Guard(
            "dcn_unpublished_has_no_sheets",
            published or not sheets,
            "An unpublished results tab yields no round sheets",
        ),
        Guard(
            "required_date_missing",
            any(
                isinstance(o.payload, SourceEventRow) and o.payload.date_raw
                for o in result.observations
            ),
            "The event document carries its source dates",
        ),
    ]
    return Accounting(
        tuple(fields),
        tuple(guards),
        sum(finals.values()),
        interpreted,
        terminal="captured_document_end" if published else None,
        # A later tab never retires earlier finals: an empty or shrunken
        # payload could be a publishing slip, so accepted rows stay
        # (review condition, 2026-10-15). The newest sheet of a round still
        # wins in projection.
        removal="none",
    )
