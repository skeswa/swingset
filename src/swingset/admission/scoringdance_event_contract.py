"""Accounting for a scoring.dance event page: its dated round listing.

Only the `scoringdance.event` parse scope names this module
(`admission/kinds.py`). An unpublished page is a dated observation, not a
withdrawal of rounds, so the page never retires accepted rows.
"""

from typing import Any

from swingset.sources.base import ParseContext, ParseResult
from swingset.sources.common import absolute

from .accounting import Accounting, account_keys
from .report import Field, Guard

VERSIONS = {"scoringdance.event": "4"}
KINDS = frozenset(VERSIONS)


def accounting(ctx: ParseContext, body: bytes, extract: Any, result: ParseResult) -> Accounting:
    if ctx.kind not in KINDS:
        raise ValueError(f"unsupported scoring.dance event contract kind: {ctx.kind}")
    fields: list[Field] = []
    account_keys(extract, {"name", "date", "rounds", "unpublished"}, "$", fields)
    rounds = extract.get("rounds", []) if isinstance(extract, dict) else []
    payload = next((o.payload for o in result.observations if o.kind == "event_sheet"), None)
    children = tuple(
        absolute(ctx.url, str(item["href"]))
        for item in rounds
        if isinstance(item, dict) and "href" in item
    )
    for index, item in enumerate(rounds):
        account_keys(item, {"id", "name", "href"}, f"rounds[{index}]", fields)
    guard = Guard(
        "required_date_missing",
        bool(isinstance(extract, dict) and extract.get("date")),
        "An event document requires its source date",
    )
    return Accounting(
        tuple(fields),
        (guard,),
        len(rounds),
        len(getattr(payload, "round_links", ())),
        children,
        tuple(watch.url for watch in result.watches),
        removal="none",
    )
