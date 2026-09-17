"""Offline parser preparation. No watches or canonical joins are emitted."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser

from swingset.sources.base import (
    ExtractError,
    JsonValue,
    Observation,
    ObservationScope,
    ParseContext,
    ParseError,
    ParseResult,
    ParseWarning,
    WatchSpec,
)
from swingset.sources.common import text
from swingset.sources.interpretation import declared
from swingset.sources.records import Cell, ResultRow, ResultTable, SourceEventRow

from .records import (
    StepRightEventSheet,
    StepRightIndexRow,
    StepRightRoundLink,
    StepRightRoundSheet,
    StepRightTable,
    callback_mark,
)

HOSTS = frozenset({"steprightsolutions.com", "www.steprightsolutions.com"})
EVENT_PATH = re.compile(r"^/events/([^/]+)(?:/round/([^/]+))?/?$")
ROUND_NAME = re.compile(r"\b(?:prelims?|semi[- ]?finals?|finals?)\b", re.I)
DATE_LINE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2}\s*(?:[-–]\s*(?:[A-Za-z]+\s+)?\d{1,2})?,?\s+\d{4}\b"
)


def _url(href: str) -> tuple[str, str, str | None] | None:
    parsed = urlsplit(urljoin("http://steprightsolutions.com/", href))
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in HOSTS:
        return None
    match = EVENT_PATH.fullmatch(parsed.path)
    if not match:
        return None
    # The archived aliases share one logical original URL; never replay URLs.
    original = urlunsplit(("http", "steprightsolutions.com", parsed.path.rstrip("/"), "", ""))
    return original, f"steprightsolutions:{match[1]}", match[2]


def _content(tree: HTMLParser) -> Any:
    return tree.css_first("main, #content, .content") or tree.body or tree.root


def _expand_judge_header(headers: tuple[Cell, ...], *, is_final: bool) -> tuple[Cell, ...]:
    """Expand only the real fixture's labeled group, keeping its source attributes."""
    grouped = [
        index
        for index, cell in enumerate(headers)
        if dict(cell.attributes).get("colspan", "1") != "1"
    ]
    if not grouped:
        return headers
    prefix = ("BIB", "Leader", "Follower") if is_final else ("BIB#", "Name")
    expected = (
        *prefix,
        "Judge Placements *" if is_final else "Judge Scores *",
        "Placement" if is_final else "Total",
    )
    if tuple(cell.text for cell in headers) != expected or grouped != [len(prefix)]:
        raise ParseError("Step Right merged header needs a reviewed fixture")
    group = headers[len(prefix)]
    attrs = dict(group.attributes)
    count = attrs.get("colspan", "")
    if not count.isdecimal() or not 1 <= int(count) <= 32:
        raise ParseError("Step Right judge group width is unsupported")
    if not is_final and re.sub(r"\s+", "", attrs.get("title", "")).lower() != "1=yes,2=alt,3=no":
        raise ParseError("Step Right grouped callback legend is unverified")
    attrs.update(colspan="1", source_group_colspan=count, source_group_label=group.text or "")
    anonymous = tuple(Cell("", tuple(sorted(attrs.items()))) for _ in range(int(count)))
    return (*headers[: len(prefix)], *anonymous, headers[-1])


class IndexPage:
    kind = "steprightsolutions.index"
    EXTRACT_VERSION = 2
    PARSER_VERSION = 2
    change_mode = "extract"

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()

    def extract(self, body: bytes) -> JsonValue:
        tree = HTMLParser(body)
        rows = []
        external_event_sites = []
        blocks = tree.css("div.event")
        date_anchors = [anchor for block in blocks for anchor in block.css("div.dates a[href]")]
        for block in blocks:
            heading = block.css_first("h4")
            location = block.css_first("div.location")
            for anchor in block.css("div.dates a[href]"):
                href = anchor.attributes["href"] or ""
                target = _url(href)
                if target is None:
                    external_event_sites.append({"label": text(anchor.text()), "url": href})
                    continue
                if target[2] is not None:
                    continue
                rows.append(
                    {
                        "url": target[0],
                        "ref": target[1],
                        "series": text(heading.text()) if heading else "",
                        "location": text(location.text()) if location else "",
                        "year": text(anchor.text()),
                    }
                )
        if not rows:
            raise ExtractError("Step Right index contains no recognized event links")
        return {
            "rows": rows,
            "contract_witness": {
                "event_block_count": len(blocks),
                "date_anchor_count": len(date_anchors),
                "external_event_sites": external_event_sites,
            },
        }

    @declared
    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        rows = extract.get("rows", []) if isinstance(extract, dict) else extract
        output = []
        seen = set()
        for row in rows:
            key = tuple(row[name] for name in ("ref", "series", "location", "year"))
            if key in seen:
                continue
            seen.add(key)
            payload = StepRightIndexRow(
                "step_right_index_row",
                row["ref"],
                row["series"],
                row["location"],
                row["year"],
                row["url"],
            )
            output.append(
                Observation(
                    ObservationScope("source_index", "steprightsolutions"), payload.kind, payload
                )
            )
        return ParseResult(tuple(output))


class EventPage(IndexPage):
    kind = "steprightsolutions.event"
    EXTRACT_VERSION = 4
    PARSER_VERSION = 3

    def extract(self, body: bytes) -> JsonValue:
        tree = HTMLParser(body)
        content = _content(tree)
        metadata_heading = content.css_first(".box-content.header h2")
        date_node = metadata_heading.css_first("small") if metadata_heading else None
        date_text = text(date_node.text()) if date_node else ""
        if metadata_heading:
            title_tree = HTMLParser(metadata_heading.html)
            for child in title_tree.css("small"):
                child.decompose()
            name = text(title_tree.text())
        else:
            headings = content.css("h1")
            name = text(headings[0].text()) if headings else ""
            breadcrumb = tree.css_first(".breadcrumb, .breadcrumbs")
            if not name and breadcrumb:
                name = text(breadcrumb.text(separator=" "))

        dates = DATE_LINE.search(text(content.text(separator=" ")))
        main_panels = content.css(".event-overview .span9 .well")
        main_panel = main_panels[0] if main_panels else None
        link_scope = main_panel or content
        contest = ""
        links = []
        for node in link_scope.traverse():
            if node.tag not in {"h1", "h2", "h3", "h4", "h5", "h6", "a"}:
                continue
            if node.tag != "a":
                contest = text(node.text())
                continue
            target = _url(node.attributes.get("href", ""))
            if target is None or target[2] is None:
                continue
            links.append(
                {
                    "contest": contest,
                    "round": text(node.text()),
                    "url": target[0],
                    "ref": target[1],
                    "round_ref": target[2],
                }
            )
        all_round_urls = []
        for anchor in content.css("a[href]"):
            target = _url(anchor.attributes.get("href", ""))
            if target is not None and target[2] is not None:
                all_round_urls.append(target[0])
        main_round_urls = [link["url"] for link in links]
        main_unique = set(main_round_urls)
        sidebar_round_urls = list((Counter(all_round_urls) - Counter(main_round_urls)).elements())
        contract_witness = {
            "main_panel_count": len(main_panels),
            "contest_count": len(main_panel.css("h1,h2,h3,h4,h5,h6")) if main_panel else 0,
            "main_round_link_count": len(main_round_urls),
            "main_unique_round_link_count": len(main_unique),
            "sidebar_duplicate_count": max(0, len(all_round_urls) - len(main_round_urls)),
            "sidebar_round_links_match_main": Counter(sidebar_round_urls)
            == Counter(main_round_urls),
            "unique_round_links_outside_main": sorted(set(all_round_urls) - main_unique),
        }
        if main_panel is not None and not links:
            raise ExtractError("Step Right event main results panel contains no round links")
        if not links:
            # The retained April 1 event capture predates the event and lists
            # metadata only. Its absence of links is not a complete enumeration.
            if metadata_heading is None or not DATE_LINE.fullmatch(date_text):
                raise ExtractError(
                    "Step Right event contains no recognized round links or metadata header"
                )
            if not name:
                raise ExtractError("Step Right metadata event has no name")
            return {
                "name": name,
                "date": date_text,
                "links": [],
                "round_listing_status": "no_round_links",
                "contract_witness": contract_witness,
            }
        return {
            "name": name,
            "date": date_text or (dates[0] if dates else None),
            "links": links,
            "round_listing_status": "listed_links",
            "contract_witness": contract_witness,
        }

    @declared
    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        refs = {link["ref"] for link in extract["links"]}
        if not refs and extract["round_listing_status"] == "no_round_links":
            target = _url(ctx.url)
            if target is None or target[2] is not None:
                raise ParseError("Step Right metadata requires its original event URL")
            refs = {target[1]}
        if len(refs) != 1 or (ctx.source_ref and ctx.source_ref not in refs):
            raise ParseError("Step Right round links disagree on event ownership")
        ref = refs.pop()
        links = tuple(
            StepRightRoundLink(row["contest"], row["round"], row["round_ref"], row["url"])
            for row in extract["links"]
        )
        payload = StepRightEventSheet(
            "step_right_event_sheet",
            ref,
            extract["name"],
            extract["date"],
            links,
            extract["round_listing_status"],
        )
        warnings = (
            ()
            if links
            else (
                ParseWarning(
                    "steprightsolutions_round_listing_absent",
                    "Event metadata has no round links; completeness and result availability remain unknown.",
                ),
            )
        )
        return ParseResult(
            (
                Observation(ObservationScope("source_event", ref), payload.kind, payload),
                Observation(
                    ObservationScope("source_index", ref),
                    "source_event_row",
                    SourceEventRow(
                        "source_event_row",
                        ref,
                        extract["name"],
                        extract["date"],
                        ctx.url,
                    ),
                ),
            ),
            warnings=warnings,
        )


class RoundPage(IndexPage):
    kind = "steprightsolutions.round"
    EXTRACT_VERSION = 2
    PARSER_VERSION = 3

    def extract(self, body: bytes) -> JsonValue:
        content = _content(HTMLParser(body))
        contest = ""
        round_name = ""
        role_heading = ""
        panel: list[str] = []
        judge_notes: list[str] = []
        chief: str | None = None
        tables = []
        for node in content.traverse():
            if node.tag not in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "table"} and not (
                node.tag == "div"
                and set(node.attributes.get("class", "").split()) & {"judges", "chief-judge"}
            ):
                continue
            visible = text(node.text(separator=" "))
            if node.tag.startswith("h"):
                if re.match(r"^(Leaders|Followers)\b", visible, re.I):
                    role_heading = visible
                elif match := ROUND_NAME.search(visible):
                    round_name = match[0]
                    if before := visible[: match.start()].strip(" :-–"):
                        contest = before
                else:
                    contest = visible
            elif node.tag != "table":
                if re.match(r"^Chief Judge\s*:", visible, re.I):
                    chief = re.sub(r"^Chief Judge\s*:\s*", "", visible, flags=re.I)
                elif re.match(r"^Judges\s*:", visible, re.I):
                    # Preserve the printed roster line. Its order never owns columns.
                    roster = HTMLParser(node.html)
                    judge_notes = [text(note.text()) for note in roster.css(".judge_note")]
                    for note in roster.css(".judge_note"):
                        note.decompose()
                    panel = [re.sub(r"^Judges\s*:\s*", "", text(roster.text()), flags=re.I)]
            else:
                rows: list[list[dict[str, Any]]] = []
                for row in node.css("tr"):
                    cells = []
                    for cell in row.iter():
                        if cell.tag not in {"th", "td"}:
                            continue
                        attrs = {
                            key: value or ""
                            for key, value in cell.attributes.items()
                            if key in {"class", "title", "colspan", "rowspan"}
                        }
                        if "class" in row.attributes:
                            attrs["row-class"] = row.attributes["class"]
                        cells.append({"text": text(cell.text(separator=" ")), "attributes": attrs})
                    if cells:
                        rows.append(cells)
                if not rows or not any(
                    re.fullmatch(r"BIB\s*#?", cell["text"], re.I) for cell in rows[0]
                ):
                    continue
                tables.append(
                    {
                        "heading": role_heading,
                        "rows": rows,
                        "panel": panel.copy(),
                        "chief": chief,
                        "judge_notes": judge_notes.copy(),
                    }
                )
                role_heading = ""
        if not tables or not contest or not round_name:
            raise ExtractError("Step Right round needs a contest, round heading, and bib table")
        return {"contest": contest, "round": round_name, "tables": tables}

    @declared
    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        target = _url(ctx.url)
        if target is None or target[2] is None or (ctx.source_ref and ctx.source_ref != target[1]):
            raise ParseError(
                "Step Right round requires an original URL with matching event ownership"
            )
        tables = []
        warnings = []
        is_final = re.fullmatch(r"finals?", extract["round"], re.I) is not None
        for number, item in enumerate(extract["tables"]):
            rows = [
                tuple(Cell(cell["text"], tuple(sorted(cell["attributes"].items()))) for cell in row)
                for row in item["rows"]
            ]
            headers = rows[0]
            headers = _expand_judge_header(headers, is_final=is_final)
            if any(len(row) != len(headers) for row in rows[1:]) or any(
                dict(cell.attributes).get(key, "1") != "1"
                for row in (headers, *rows[1:])
                for cell in row
                for key in ("colspan", "rowspan")
            ):
                raise ParseError("Step Right merged or uneven cells need a reviewed fixture")
            columns = tuple(index for index, cell in enumerate(headers) if not cell.text)
            if not columns:
                warnings.append(
                    ParseWarning(
                        "steprightsolutions_anonymous_columns_unknown",
                        "No blank judge-column headers recognized.",
                        {"table": number},
                    )
                )
            if not is_final:
                warnings.append(
                    ParseWarning(
                        "steprightsolutions_promotion_unknown",
                        "Prelims promotion marking is unverified.",
                        {"table": number},
                    )
                )
                unknown = sorted(
                    {
                        row[index].text or ""
                        for row in rows[1:]
                        for index in columns
                        if callback_mark(row[index].text or "") is None
                    }
                )
                if unknown:
                    warnings.append(
                        ParseWarning(
                            "steprightsolutions_callback_unknown",
                            "Unverified callback values retained without a mark interpretation.",
                            {"table": number, "values": unknown},
                        )
                    )
            heading = str(item["heading"])
            ownership = (
                "unknown"
                if is_final
                else "leader"
                if re.match(r"^Leaders\b", heading, re.I)
                else "follower"
                if re.match(r"^Followers\b", heading, re.I)
                else "unknown"
            )
            if ownership == "unknown":
                warnings.append(
                    ParseWarning(
                        "steprightsolutions_bib_ownership_unknown",
                        "Printed bib remains attached to its source row; no person owns it yet.",
                        {"table": number},
                    )
                )
            table = ResultTable(heading, headers, tuple(ResultRow(row) for row in rows[1:]))
            tables.append(
                StepRightTable(
                    table,
                    columns,
                    tuple(f"anon-{index + 1}" for index in range(len(columns))),
                    tuple(item["panel"]),
                    item["chief"],
                    bib_ownership=ownership,
                    judge_notes_raw=tuple(item["judge_notes"]),
                )
            )
        payload = StepRightRoundSheet(
            "step_right_round_sheet",
            target[1],
            target[2],
            extract["contest"],
            extract["round"],
            tuple(tables),
            "unknown" if is_final else "legacy_3",
        )
        return ParseResult(
            (Observation(ObservationScope("source_event", target[1]), payload.kind, payload),),
            warnings=tuple(warnings),
        )


@dataclass(frozen=True)
class StepRightSource:
    name: str = "steprightsolutions"
    hosts: frozenset[str] = HOSTS
    page_kinds = {
        IndexPage.kind: IndexPage(),
        EventPage.kind: EventPage(),
        RoundPage.kind: RoundPage(),
    }

    def seed_watches(self, config: object, overrides: object) -> list[WatchSpec]:
        return []


SOURCE = StepRightSource()
