# D-0258: Read SwingDirector score PDFs from event sites, one reviewed row per PDF

Recorded: 2026-10-15  
Decided by: agent, 2026-10-15, under D-0172; source: session instruction of 2026-10-15 to cover the 2026 events that post results only on their own sites  
Topic: Source parsing  
Supersedes: in part, the long-tail guide's `generic.pdf_table` plan for these sites  
Superseded by: —

## Decision

- A new source, `swingdirector`, reads the score PDFs that the
  SwingDirector and CountryDanceDirector scoring tool prints, wherever an
  event site posts them. Its one page kind, `swingdirector.round`, reads text
  positions, keeps marks, ordinals, tallies and places as printed, and
  fails closed on any other layout. Pages another tool printed inside a
  merged file stay visible as unsupported pages.
- Each PDF is one `overrides/source_urls.csv` row with `source =
swingdirector`; the row's `event_id` names the source event
  (`swingdirector:<event_id>`). No page listing is crawled.
- The source ships disabled (no `[sources.swingdirector]` block) and its
  contract unassessed. The event-site hosts use the default politeness
  table; no `hosts.toml` block is added.

## Why

High Desert Dance Classic, South Bay Dance Fling and Calgary Dance Stampede
publish 2026 WSDC sheets as PDFs from the same tool: 42 PDFs with 19
division finals. All 10 captured 2026 files of that tool parse with one layout
(the other 32 are **unverified**), and every captured final's tallies
recompute from its ordinals and every judge column ranks each finalist
once, which together place each ordinal under its judge. The sites'
pages also list other years and other dances in one page, so a listing
crawl would attach 2022-2025 files to the 2026 event; a reviewed row per
PDF does not.

## Alternatives and consequences

A generic `pdfplumber` table reader was planned; it is not a dependency,
and `pypdf`, already one, gives the text positions this layout needs. Cost: 42 override rows for 2026, and
prelim sheets print no callback decision, so who advanced stays unknown
until a projector derives it from the next round's roster.

## Links

[Long-tail guide, SwingDirector PDFs](../../docs/reference/sources/long-tail.md#swingdirector-score-pdfs)
