# D-0080: Parse only owned legacy DCN results tables

Recorded: 2026-09-17  
Decided by: agent  
Topic: Offline DCN legacy results interpretation  
Supersedes: —  
Superseded by: —

## Decision

Add a separate offline `dcn.legacy_results` adapter for the audited Riga body.
Bind the event through its canonical, OpenGraph, navigation and selector IDs.
Bind the selected contest heading to exactly one printed selector and each
round table to its own sibling legend and round container. Reject unfamiliar
layout and ownership rather than selecting a nearby page-global label.

Retain row bibs, names and placement intervals as source observations. Finals
pair names keep their printed order without individual role assignment; their
bib belongs to the row. Preliminary roles come only from explicit table labels.
Promotion and population completeness remain unknown, including empty tables.
Contest selectors and PDF links remain inert locators with no acquisition or
PDF interpretation implied.

## Why

The acquired page shows one selected Newcomer contest, nine finals pairs, an
empty printed preliminary leaders table and eight followers rows. It does not
show every listed contest or explain the missing leaders. HTML5 parsing moves
invalid table legends into their table's parent column. Exact container
ownership is needed to prevent assigning an adjacent round or role to rows.
Printed placement intervals and bibs do not establish promotion, judge marks,
individual finals bib ownership, or score-PDF semantics.

## Consequences

Keep the new kind outside the ordinary source registry, with no seed or child
watches, source-kind activation or canonical projector. Preserve the captured
body and independent audit unchanged. New real, malformed, empty and changed
controls test source ownership, table geometry, typed codec and consumed input
changes. Modern results payloads and preliminary/final PDF parsing remain
separate unfinished requirements; they need suitable real controls and review.
This local parser choice does not accept a historical year or public release.

## Links

- [Riga fixture approval](0073-approve-exact-riga-results-html-fixture.md)
- [Source contract](../../docs/reference/sources/danceconvention.md)
- [Implementation and evidence](../investigations/2026/dcn-legacy-results-parsing-2026-09-17.md)
