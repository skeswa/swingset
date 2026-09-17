"""One reviewed legacy results layout; source facts and inert locators only."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urljoin, urlsplit

from selectolax.parser import HTMLParser, Node

from swingset.sources.base import (
    ExtractError,
    JsonValue,
    Observation,
    ObservationScope,
    ParseContext,
    ParseError,
    ParseResult,
    ParseWarning,
)
from swingset.sources.common import text

from .nuxt import MAX_BODY_BYTES
from .records import DcnContestLocator, DcnLegacyResults, DcnResultRow, DcnResultTable

ROOT = "https://danceconvention.net"
SELECTOR = re.compile(r"/eventdirector/en/eventpage:selectresultscontestrow/([1-9][0-9]{0,17})")
PDF = re.compile(r"/eventdirector/en/roundscores/[1-9][0-9]{0,17}\.pdf")
PLACEMENT = re.compile(r"([1-9][0-9]{0,5})(?:-([1-9][0-9]{0,5}))?")
TABLES = (("Finals", None), ("Prelims - leaders", "leader"), ("Prelims - followers", "follower"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ExtractError("DCN legacy results " + message)


def children(node: Node) -> list[Node]:
    return list(node.iter(include_text=False))


def source_url(href: str) -> tuple[str, str, str]:
    require(
        not any(char.isspace() or ord(char) < 32 for char in href),
        "locator has whitespace or controls",
    )
    try:
        parsed = urlsplit(urljoin(ROOT, href))
    except ValueError as exc:
        raise ExtractError("DCN legacy results locator is malformed") from exc
    require(
        parsed.scheme == "https" and parsed.netloc == "danceconvention.net" and not parsed.fragment,
        "locator has foreign ownership",
    )
    return parsed.geturl(), parsed.path, parsed.query


def selector(node: Node, event_ref: str) -> dict[str, str]:
    from .adapter import checked_text, event_locator

    url, path, query = source_url(node.attributes.get("href") or "")
    match = SELECTOR.fullmatch(path)
    require(
        match is not None and node.attributes.get("data-update-zone") == "tabsZone",
        "contest selector changed",
    )
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ExtractError("DCN legacy results selector query is malformed") from exc
    require(len(pairs) == 1 and pairs[0][0] == "t:ac", "contest selector context changed")
    ref, _, tab = event_locator("/eventdirector/en/eventpage/" + pairs[0][1])
    require(ref == event_ref and tab == "results", "contest selector event ownership disagrees")
    assert match is not None
    return {
        "contest_id": match[1],
        "name": checked_text(text(node.text()), "contest name"),
        "url": url,
    }


def result_rows(table: Node, *, pair: bool) -> list[dict[str, JsonValue]]:
    from .adapter import checked_text

    require([child.tag for child in children(table)] == ["tbody"], "table sections changed")
    rows = table.css("tr")
    require(1 <= len(rows) <= 2001, "table row count is outside the reviewed bound")
    header = children(rows[0])
    require(
        [node.tag for node in header] == ["th", "th", "th"]
        and [text(node.text()) for node in header] == ["Bib", "Names", "Placement"],
        "table headers changed",
    )
    require(
        all(
            cell.attributes.get("rowspan", "1") == "1"
            and cell.attributes.get("colspan", "1") == "1"
            for cell in header
        ),
        "merged header cells are unsupported",
    )
    result = []
    seen = set()
    for row in rows[1:]:
        cells = children(row)
        require([cell.tag for cell in cells] == ["td", "td", "td"], "result row cell count changed")
        require(
            all(
                cell.attributes.get("rowspan", "1") == "1"
                and cell.attributes.get("colspan", "1") == "1"
                for cell in cells
            ),
            "merged result cells are unsupported",
        )
        bib, names, rank = text(cells[0].text()), cells[1].text().strip(), text(cells[2].text())
        require(
            re.fullmatch(r"[0-9]{1,12}", bib) is not None and bib not in seen,
            "row bib is missing, changed or repeated",
        )
        seen.add(bib)
        match = PLACEMENT.fullmatch(rank)
        require(match is not None, "placement is not an integer or printed interval")
        assert match is not None
        low, high = int(match[1]), int(match[2] or match[1])
        require(low <= high, "placement interval is reversed")
        checked_text(names, "result names")
        # Pair separator is the source's explicit newline followed by '- '. A
        # hyphen inside a person's name is never a partner delimiter.
        members = re.split(r"\r?\n[ \t]*-[ \t]+", cells[1].text())
        require(len(members) == (2 if pair else 1), "name cell pair structure changed")
        normalized = [checked_text(name.strip(), "member name") for name in members]
        result.append(
            dict(bib=bib, names=names, members=normalized, placement=rank, low=low, high=high)
        )
    return result


class LegacyResultsPage:
    kind = "dcn.legacy_results"
    EXTRACT_VERSION = 1
    PARSER_VERSION = 1
    change_mode = "extract"

    def expected_statuses(self, watch: object) -> frozenset[int]:
        return frozenset()

    def extract(self, body: bytes) -> JsonValue:
        from .adapter import LegacyEventPage, checked_text, event_locator

        require(len(body) <= MAX_BODY_BYTES, "body exceeds byte bound")
        try:
            tree = HTMLParser(body.decode("utf-8"))
        except UnicodeError as exc:
            raise ExtractError("DCN legacy results HTML is not UTF-8") from exc
        metadata = LegacyEventPage().extract(body)
        canonical = tree.css('link[rel="canonical"]')
        og = tree.css('meta[property="og:url"]')
        require(len(canonical) == len(og) == 1, "canonical event identity missing or repeated")
        ref, canonical_url, tab = event_locator(canonical[0].attributes.get("href") or "")
        og_ref, _, og_tab = event_locator(og[0].attributes.get("content") or "")
        require(
            tab == "results"
            and og_ref == ref
            and og_tab is None
            and len(metadata["results_links"]) == 1
            and metadata["results_links"][0]["ref"] == ref,
            "event ownership disagrees",
        )
        zones = tree.css("div#resultsDownloadZone")
        panes = tree.css("div.tab-pane.active")
        require(len(zones) == len(panes) == 1, "selected results zone missing or repeated")
        zone, pane = zones[0], panes[0]
        owner = zone.parent
        require(
            owner is not None
            and owner.tag == "div"
            and owner.parent is not None
            and owner.parent.parent is not None
            and owner.parent.parent.mem_id == pane.mem_id,
            "selected contest container changed",
        )
        assert owner is not None and owner.parent is not None
        columns = children(owner.parent)
        require(
            len(columns) == 2 and columns[1].mem_id == owner.mem_id,
            "contest column ownership changed",
        )
        links = columns[0].css("a[href]")
        require(1 <= len(links) <= 200, "contest inventory is empty or oversized")
        selectors = [selector(node, ref) for node in links]
        require(
            len({item["contest_id"] for item in selectors}) == len(selectors),
            "duplicate contest selector",
        )
        headings = zone.css("h3")
        require(len(headings) == 1, "selected contest heading missing or repeated")
        selected = checked_text(text(headings[0].text()), "selected contest")
        matches = [item for item in selectors if item["name"] == selected]
        require(len(matches) == 1, "selected contest does not own one printed selector")
        sections = children(owner)
        require(
            len(sections) == 3
            and sections[0].mem_id == zone.mem_id
            and all(
                section.tag == "div" and section.attributes.get("class") == "row"
                for section in sections[1:]
            ),
            "round container layout changed",
        )
        owned_tables = owner.css("table")
        require(
            len(owned_tables) == 3
            and {node.mem_id for node in tree.css("table")}
            == {node.mem_id for node in owned_tables},
            "table ownership or population changed",
        )
        tables = []
        expected_groups = (TABLES[:1], TABLES[1:])
        for section, expected in zip(sections[1:], expected_groups, strict=True):
            table_columns = children(section)
            require(len(table_columns) == len(expected) + 1, "round columns changed")
            pdf_links = table_columns[-1].css("a[href]")
            require(
                len(pdf_links) == len(section.css("a[href]")) == 1
                and text(pdf_links[0].text()) == "Round scores (PDF)",
                "round PDF locator ownership changed",
            )
            pdf_url, pdf_path, query = source_url(pdf_links[0].attributes.get("href") or "")
            require(PDF.fullmatch(pdf_path) is not None and not query, "round PDF locator changed")
            for column, (label, role) in zip(table_columns[:-1], expected, strict=True):
                # HTML5 foster-parents an invalid table/legend into this sibling
                # column. Never choose a page-global or preceding round label.
                nodes = children(column)
                require(
                    [node.tag for node in nodes] == ["legend", "table"]
                    and text(nodes[0].text()) == label,
                    "round label/table ownership changed",
                )
                tables.append(
                    dict(
                        label=label,
                        role=role,
                        rows=result_rows(nodes[1], pair=role is None),
                        pdf_url=pdf_url,
                    )
                )
        require(
            tables[0]["pdf_url"] != tables[1]["pdf_url"],
            "distinct rounds share one unexpected PDF locator",
        )
        return dict(
            source_event_ref=ref,
            canonical_url=canonical_url,
            event_name=metadata["name"],
            contest_id=matches[0]["contest_id"],
            contest_name=selected,
            selectors=selectors,
            tables=tables,
        )

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult:
        from .adapter import context_matches, event_locator

        context_matches(ctx, self.kind)
        try:
            ref, _, tab = event_locator(ctx.url)
        except ExtractError as exc:
            raise ParseError(str(exc)) from exc
        if (
            tab != "results"
            or ref != extract["source_event_ref"]
            or (ctx.source_ref is not None and ctx.source_ref != ref)
        ):
            raise ParseError("DCN legacy results parse ownership disagrees")
        payload = DcnLegacyResults(
            "dcn_legacy_results",
            ref,
            extract["event_name"],
            extract["contest_id"],
            extract["contest_name"],
            tuple(
                DcnContestLocator(row["contest_id"], row["name"], row["url"])
                for row in extract["selectors"]
            ),
            tuple(
                DcnResultTable(
                    row["label"],
                    row["role"],
                    tuple(
                        DcnResultRow(
                            item["bib"],
                            item["names"],
                            tuple(item["members"]),
                            item["placement"],
                            item["low"],
                            item["high"],
                        )
                        for item in row["rows"]
                    ),
                    row["pdf_url"],
                )
                for row in extract["tables"]
            ),
        )
        return ParseResult(
            (Observation(ObservationScope("source_event", ref), payload.kind, payload),),
            warnings=(
                ParseWarning(
                    "dcn_results_population_unknown",
                    "Only the selected contest's printed rows are observed; empty roles and listed contests do not prove complete populations or promotion.",
                ),
                ParseWarning(
                    "dcn_score_locators_unassessed",
                    "Printed round PDF and contest selectors are inert locators; score interpretation and acquisition remain unassessed.",
                ),
                ParseWarning(
                    "dcn_canonical_admission_pending",
                    "Offline source observations; kind admission and canonical projection remain pending.",
                ),
            ),
        )
