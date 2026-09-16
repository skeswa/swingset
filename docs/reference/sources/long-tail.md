# Long tail: event sites, Google Drive, custom apps, UCWDC

Some results live on individual event sites rather than a supported platform. This guide describes how to review those sources and preserve their limits.

[All sources](README.md) · [Shared fetching rules](../fetching.md)

## 1. Status

Facts from the 2026-09 research (`journal/evidence/collection/source-survey-2026-09-04/results-sources.csv`,
`journal/evidence/collection/source-survey-2026-09-04/results-sources.overrides.csv`). 15 of 177 held events in the
last year published outside the four platforms: 7 on the event's own
site, 2 in Google Drive folders, 6 elsewhere (UCWDC PDFs, a blog, a
custom scoring app). 4 more were not found at all and probably live in
Facebook groups, which we do not fetch.

## 2. Principle

No per-site parser is written for a single event. The long tail is
served by two generic adapters plus an overrides file:

- `generic.html_table`: fetch one URL, read every `<table>` by header
  text, emit contests with `parse_status = unsupported` when the
  headers are not recognized. Recognized header sets are added to a
  small vocabulary file as they are met.
- `generic.pdf_table`: fetch one PDF, `pdfplumber` tables, same
  vocabulary.
- `overrides/source_urls.csv`: `event_id, source, kind, url, parser, notes`.
  A row is the only thing needed to cover a long-tail event. The
  research CSV is the seed.

A row's `url` may be a template with `<run_uuid>`-style placeholders
only for the custom-app adapters below.

## 3. Known cases

| Case                                                                                    | What exists                                                                                                                                                                                                                                                                                               | Adapter                                     | Notes                                                                                                                                                                                                                                                                             |
| --------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Swing Fiction (Czech Republic)                                                          | JSON API at `api.swingfiction.cz`: `GET /api/events/swifi/public` (event uuids), `GET /api/event/<uuid>/competition/runs/public` (runs with `publishResults`), `GET /api/event/<uuid>/competition/run/<run_uuid>/public` (competitors with bib, heat, role, partner, alternate flag, orderNo, finalScore) | `swingfiction.api` (small, JSON)            | Found by reading the site's bundle. Not documented by the operator, so ask before polling; 4 requests per event per poll. Judges not listed; whether marks are public is **unverified**.                                                                                          |
| Mountain Magic, New England Dance Festival                                              | Public Google Drive folders, one PDF per division and round                                                                                                                                                                                                                                               | `google_drive.folder` + `generic.pdf_table` | Folder listing: `https://drive.google.com/embeddedfolderview?id=<folderId>#list` is static HTML (**unverified** that it still works without JS); file download `https://drive.google.com/uc?export=download&id=<fileId>`. Google is not a friend's server; 5 s gap still applies. |
| Texas Classic, Chicagoland, Colorado Country Classic                                    | UCWDC results PDFs, multi-dance entries                                                                                                                                                                                                                                                                   | none for now                                | Country-dance events that bundle WCS; the contest and round model does not fit. Recorded as `contests` rows with `parse_status = unsupported` if fetched at all.                                                                                                                  |
| Florida Classic Series blog, Charlotte WestieFest page, other event sites               | HTML or PDF on the event site                                                                                                                                                                                                                                                                             | `generic.html_table` or `generic.pdf_table` | One override row each.                                                                                                                                                                                                                                                            |
| Cash Bash 2025, WesterOz 2026, Canadian Swing Championships 2026, Next Level Swing 2026 | not found; Facebook groups likely                                                                                                                                                                                                                                                                         | none                                        | Listed in the dataset card as gaps.                                                                                                                                                                                                                                               |

## 4. Discovery

The event-site link scan (daily from 14 days before the event to 30
days after, stopping once results watches exist) looks for: links to the four platforms, links to `drive.google.com/drive/folders/`,
and links whose text contains "results", "scores", or "callbacks". Hits
go to the review queue as suggested override rows; nothing is fetched
automatically beyond the platforms.

## 5. Change detection

Event sites and Drive: conditional GET where validators exist (Drive
serves ETags, **unverified**), raw body hash otherwise. Long-tail
watches use the `cooling` schedule from the start (6 h, doubling to
24 h): these are posted after the event, not live.

## 6. Politeness settings

The defaults in `docs/reference/fetching.md` (5 s gap, 200 requests a day per
host), no overrides. Event sites are small WordPress or Squarespace
installs; a long-tail event costs a few dozen requests in total.

## 7. LLM-assisted extraction

For PDFs and pages the generic adapters mark `unsupported`, and for
photographed score sheets, a local model may draft records: Ollama with
a JSON schema (`format`), or `llama.cpp` with a GBNF grammar, and
Qwen2.5-VL for images. The draft is written to the review queue with
`link_status = draft` and is never published until a person accepts it
by adding the rows to overrides. This is a manual tool, not a pipeline
stage; see `journal/investigations/2026/scraping-techniques.md` section 10.

## 8. Open items

- Whether Swing Fiction's operator is fine with API polling.
- Whether the Drive embedded folder view still renders server-side.
- A vocabulary of header sets for `generic.html_table` and
  `generic.pdf_table`, grown from fixtures.
