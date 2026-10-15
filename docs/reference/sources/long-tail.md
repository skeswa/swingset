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

A 2026-10-15 review fetched the five 2026 WSDC events that post results only
on their own sites ([D-0258](../../../journal/decisions/0258-read-swingdirector-score-pdfs-from-event-sites.md)).
Three post SwingDirector PDFs, now read by the `swingdirector` adapter
([below](#swingdirector-score-pdfs)). Swing Fiction's API has marks but no
results. Canadian Swing Championships posted no 2026 results.

## 2. Principle

No per-site parser is written for a single event. The long tail is
served by two generic adapters plus an overrides file:

- `generic.html_table`: fetch one URL, read every `<table>` by header
  text, emit contests with `parse_status = unsupported` when the
  headers are not recognized. Recognized header sets are added to a
  small vocabulary file as they are met.
- `generic.pdf_table`: fetch one PDF, read its tables, same
  vocabulary. Not built; a PDF tool shared by several events gets its own
  adapter instead (`swingdirector`).
- `overrides/source_urls.csv`: `event_id, source, kind, url, parser, notes`.
  A row is the only thing needed to cover a long-tail event. The
  research CSV is the seed.

A row's `url` may be a template with `<run_uuid>`-style placeholders
only for the custom-app adapters below.

## 3. Known cases

| Case                                                                            | What exists                                                                                                                                                                                                                                                                                                                                                   | Adapter                                     | Notes                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Swing Fiction (Czech Republic)                                                  | JSON API at `api.swingfiction.cz`: `GET /api/events/swifi/public` (event uuids: 2024, 2025, 2026), `GET /api/event/<uuid>/competition/runs/public` (runs with `publishResults`), `GET /api/event/<uuid>/competition/run/<run_uuid>/public` (competitors with bib, heat, role, partner, alternate and backup flags; judges with full names and every raw mark) | none yet                                    | Verified 2026-10-15: 11 runs, 5 divisions (Newcomer to All-Star), prelim marks `10/4.5/4.3/4.2/0`, final ordinals keyed by leader bib. `finalScore` is empty: no placement and no callback decision is published (the site presumably computes both in the browser, **unverified**). Not documented by the operator; robots.txt is a 404. Needs a reviewed relative-placement rule and the operator's consent. |
| Mountain Magic, New England Dance Festival                                      | Public Google Drive folders, one PDF per division and round                                                                                                                                                                                                                                                                                                   | `google_drive.folder` + `generic.pdf_table` | Folder listing: `https://drive.google.com/embeddedfolderview?id=<folderId>#list` is static HTML (**unverified** that it still works without JS); file download `https://drive.google.com/uc?export=download&id=<fileId>`. Google is not a friend's server; the 2 s floor still applies.                                                                                                                        |
| Texas Classic, Chicagoland, Colorado Country Classic                            | UCWDC results PDFs, multi-dance entries                                                                                                                                                                                                                                                                                                                       | none for now                                | Country-dance events that bundle WCS; the contest and round model does not fit. Recorded as `contests` rows with `parse_status = unsupported` if fetched at all.                                                                                                                                                                                                                                               |
| Florida Classic Series blog, Charlotte WestieFest page, other event sites       | HTML or PDF on the event site                                                                                                                                                                                                                                                                                                                                 | `generic.html_table` or `generic.pdf_table` | One override row each.                                                                                                                                                                                                                                                                                                                                                                                         |
| Cash Bash 2025, WesterOz 2026, Next Level Swing 2026                            | not found; Facebook groups likely                                                                                                                                                                                                                                                                                                                             | none                                        | Listed in the dataset card as gaps.                                                                                                                                                                                                                                                                                                                                                                            |
| High Desert Dance Classic, South Bay Dance Fling, Calgary Dance Stampede (2026) | SwingDirector or CountryDanceDirector score PDFs on the event site                                                                                                                                                                                                                                                                                            | `swingdirector.round`                       | One override row per PDF; see [SwingDirector score PDFs](#swingdirector-score-pdfs).                                                                                                                                                                                                                                                                                                                           |
| Canadian Swing Championships 2026                                               | The Wix results page links "2025 Results" to a danceplace.com event page; the 2026 danceplace page has an empty results tab                                                                                                                                                                                                                                   | none                                        | Checked 2026-10-15; nothing to read. Probably a Facebook group.                                                                                                                                                                                                                                                                                                                                                |

### SwingDirector score PDFs

SwingDirector and its sibling CountryDanceDirector score an event and print
one PDF per round sheet: a footer names the tool. Event sites post these
files. Verified 2026-10-15 (D-0258):

| Event (2026)              | Where                                                  | WSDC PDFs                                                         | Read                                                                                                                |
| ------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| High Desert Dance Classic | `highdesertdanceclassic.com/pdf/Results 2026/`, IIS    | 20: Novice to All-Star/Champions, Masters, Sophisticated, Juniors | 5 sampled, all parse                                                                                                |
| South Bay Dance Fling     | `sbdf.dance/wp-content/uploads/2026/09/`, WordPress    | 20: Novice to All-Star/Champions, Masters, Sophisticated          | 3 sampled, all parse; the page also lists 2022-2025 files                                                           |
| Calgary Dance Stampede    | `calgarydancestampede.com/wp-content/uploads/2026/04/` | 3 merged files                                                    | Finals: 6 divisions parse. Prelims: 8 sheets parse, pages 3-6 come from another tool. Semifinals: another tool only |

Robots: none on the IIS host (404); the WordPress hosts disallow only
`/wp-admin/`. Site terms were not checked (**unverified**). Older files
differ: the SBDF 2025 Novice final is an image with no text, and HDDC's
2025 page loads by script.

The sheet layouts are fixed:

- Prelims and semifinals: `Heat`, `Bib`, `Contestant(s)`, then one column
  per judge, whose name is printed rotated. Marks are `Y`, `N`, `A1`-`A3`.
  The sheet prints no callback decision; the `Heat` column is blank.
  A title ends in `(Leaders)` or `(Followers)`. Long sheets continue on a
  second page under the same title.
- Finals (title ends `- Scores`): `Bib`, `Entry` (`LEADER / FOLLOWER`; the
  bib is the leader's), one ordinal per judge, relative-placement tallies
  `C1`..`Cn` printed as `count(sum)`, and `Place`.
- Hidden entrants print `(Name hidden for discretion)` and keep their bib.

`swingdirector.round` reads text positions with `pypdf`, so wrapped names,
wrapped tally cells and rotated judge names land in their columns. In a
final it recomputes every tally from the row's ordinals, which proves the
row was read in full, and requires each judge's column to rank every
finalist once, which proves each ordinal sits under its own judge; either
mismatch refuses the sheet. A page without the footer is reported as
unsupported, and a SwingDirector page whose layout cannot be read as
failed, with its reason; the file's other sheets are kept, as the parsing
rules require. A file with no readable SwingDirector page fails
extraction, and so does one that exceeds a bound: 8 MiB, 40 pages, 1 MiB of
decoded content per page, 4,000 text items per page, or 20 seconds of
wall-clock time. Observations are
`swingdirector_round_sheet` records in source vocabulary. Contract 1
(`admission/swingdirector_contract.py`) recounts the bib rows of every sheet
page, re-checks prelim marks, tallies, places and judge rankings, and
blocks a file with an unreadable SwingDirector page; foreign pages, hidden
names and unprinted callbacks are bounded findings. A re-fetched PDF
replaces only its own sheets. It stays in shadow until reviewed.

Each PDF is one override row: `source = swingdirector`, `parser =
swingdirector.round`, the event's `event_id`. The adapter's own seed gives
the watch the reference `swingdirector:<event_id>`, which the generic row
handling cannot, and the map reads the same reference from the row's
`event_id`, so no alias row is needed (D-0259). PDFs are static files with
`ETag` and `Last-Modified`, so the page kind uses conditional GETs
(`validators`). The 42 proposed 2026 rows (20 HDDC, 20 SBDF, the Calgary
finals and prelims files; the Calgary semifinals file holds no SwingDirector
page) were drafted on 2026-10-15 and wait for review.

`project/swingdirector.py` turns an event's sheets into the round sheets
the contest projector reads (D-0259):

- One contest per sheet title before ` - <round>`; rounds by stage
  (prelim, quarterfinal, semifinal, final). A final prints `Scores`, so its
  round is named `Final`.
- Prelim and semifinal rows: one entry per bib in the title's role, with
  each judge's `Y`, `A1`-`A3` or `N` as a callback mark. Final rows: the
  leader with the printed bib, the follower by name only, joined to the
  follower's earlier entry by the projector's name rule; ordinals become
  final marks, `C1..Cn` counts the tally, `Place` the placement.
- Judges are keyed by the printed form (`C. REED`) per event. A name
  suppression covers that form at build time.
- Who advanced comes from the contest's next stage: same-role bibs into a
  semifinal; into a final, leader bibs and follower names. It is derived only
  when every next-round entrant matches one row and at most one row that did
  not advance outscores the lowest row that did (Y 10, A1 4.5, A2 4.3,
  A3 4.2). Rows below that cut are `eliminated`; others that did not advance
  (a tie-break or a scratch) have no outcome. Otherwise, or when a stage is
  missing for the role, the round keeps its marks without outcomes and
  `promoted_count` is null.
- A bib printed hidden in any round is nameless in every round of its
  contest and role and publishes no marks or callback; a hidden side of a
  final pair is left out.

Watching the results pages instead of 42 rows is designed, not built. A
page kind `swingdirector.index` would read one reviewed page per event (the
HDDC 2026 page, `sbdf.dance/results/`, `calgarydancestampede.com/results/`)
and list only links that match the event's reviewed file prefix, for
example `pdf/Results 2026/WSDC ` or `wp-content/uploads/2026/09/SBDF2026_WSDC`,
as child `swingdirector.round` watches under `swingdirector:<event_id>`. The
prefix keeps other years and dances out, which is why D-0258 chose rows. The
pages send no validators, so the watch hashes the body on the `cooling`
schedule and stops 30 days after the event. That is three reviewed rows and
a prefix per event instead of one row per PDF.

## 4. Discovery

The event-site link scan below is planned, not built; the offline
[event-site link report](../../../journal/tools/collection/event_site_links.py)
covers WDR links for past events (D-0256). The scan (daily from 14 days
before the event to 30 days after, stopping once results watches exist)
would look for: links to the four platforms, links to `drive.google.com/drive/folders/`,
and links whose text contains "results", "scores", or "callbacks". Hits
go to the review queue as suggested override rows; nothing is fetched
automatically beyond the platforms.

## 5. Change detection

Event sites and Drive: conditional GET where validators exist (Drive
serves ETags, **unverified**), raw body hash otherwise. Long-tail
watches use the `cooling` schedule from the start (6 h, doubling to
24 h): these are posted after the event, not live.

## 6. Politeness settings

The defaults in `docs/reference/fetching.md` (2 s gap, 200 requests a day per
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
- SwingDirector rollout: a review of contract 1, the `[sources.swingdirector]`
  block, and the 42 override rows. The projector and the judge suppression
  rule are built (D-0259). Identity references read only `round_sheet`
  observations, so an identity override cannot yet cite a SwingDirector row.
- Swing Fiction: a relative-placement rule to turn its ordinals into places,
  and the operator's consent.
