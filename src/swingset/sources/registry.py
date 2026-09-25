"""Which adapter module owns each source and page kind.

The tables are explicit so that resolving one page kind imports only its
owner. Nothing here imports an adapter at module level.
"""

from __future__ import annotations

import importlib
from typing import cast

from .base import PageKind, Source, WatchSpec

# Registration order is the order `sources()` returns.
ADAPTERS: dict[str, str] = {
    "wsdc_calendar": "swingset.sources.wsdc_calendar.adapter",
    "wsdc_registry": "swingset.sources.wsdc_registry.adapter",
    "eepro": "swingset.sources.eepro.adapter",
    "scoringdance": "swingset.sources.scoringdance.adapter",
    "wdr": "swingset.sources.wdr.adapter",
    "swingdancecouncil": "swingset.sources.swingdancecouncil.adapter",
    "wsdc_newsletter": "swingset.sources.wsdc_newsletter.adapter",
    "steprightsolutions": "swingset.sources.steprightsolutions.adapter",
}

KIND_OWNERS: dict[str, str] = {
    "wsdc_calendar.events": "wsdc_calendar",
    "wsdc_registry.dancer": "wsdc_registry",
    "eepro.autoindex": "eepro",
    "eepro.index": "eepro",
    "eepro.round": "eepro",
    "scoringdance.event": "scoringdance",
    "scoringdance.recent": "scoringdance",
    "scoringdance.round": "scoringdance",
    "scoringdance.sitemap": "scoringdance",
    "wdr.awards": "wdr",
    "wdr.rounds": "wdr",
    "swingdancecouncil.events": "swingdancecouncil",
    "wsdc_newsletter.events": "wsdc_newsletter",
    "wsdc_newsletter.index": "wsdc_newsletter",
    "steprightsolutions.event": "steprightsolutions",
    "steprightsolutions.index": "steprightsolutions",
    "steprightsolutions.round": "steprightsolutions",
}


def source(name: str) -> Source:
    """Import one registered adapter and return its source."""
    return cast(Source, importlib.import_module(ADAPTERS[name]).SOURCE)


def sources() -> tuple[Source, ...]:
    return tuple(source(name) for name in ADAPTERS)


def get_page_kind(name: str) -> PageKind:
    owner = KIND_OWNERS.get(name)
    if owner is None:
        raise KeyError(f"unknown page kind: {name}")
    return source(owner).page_kinds[name]


def seed_watches(config: object, overrides: object) -> list[WatchSpec]:
    result: list[WatchSpec] = []
    for item in sources():
        result.extend(item.seed_watches(config, overrides))
    return result
