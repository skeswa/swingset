"""Classify actual parser purposes independently of transport-shaped watch kinds."""

from typing import Literal

type Purpose = Literal["result", "event_index", "discovery"]

_PURPOSES: dict[str, Purpose] = {
    "eepro.round": "result",
    "scoringdance.round": "result",
    "wdr.rounds": "result",
    "wdr.awards": "result",
    "steprightsolutions.round": "result",
    "eepro.autoindex": "event_index",
    "scoringdance.event": "event_index",
    "steprightsolutions.event": "event_index",
    "eepro.index": "discovery",
    "scoringdance.sitemap": "discovery",
    "scoringdance.recent": "discovery",
    "steprightsolutions.index": "discovery",
    "wsdc_calendar.events": "discovery",
    "swingdancecouncil.events": "discovery",
    "wsdc_newsletter.events": "discovery",
    "wsdc_newsletter.index": "discovery",
    "wsdc_registry.dancer": "discovery",
}
SOURCE_EVENT_PARSERS = frozenset(
    parser for parser, kind in _PURPOSES.items() if kind != "discovery"
)


def purpose(parser: str) -> Purpose | None:
    """Unknown parsers stay unclassified; this grants no interpretation authority."""
    return _PURPOSES.get(parser)


def is_source_event_request(
    *, source: str, parser: str, watch_kind: str, source_ref: str | None
) -> bool:
    """Recognize source event pages; keep unknown legacy event hints unassessed."""
    if not source_ref or source_ref.endswith(":unknown") or parser.partition(".")[0] != source:
        return False
    kind = purpose(parser)
    if kind is not None:
        return kind in {"result", "event_index"}
    return watch_kind in {"event", "round"}
