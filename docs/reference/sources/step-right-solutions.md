# Step Right Solutions (`steprightsolutions.com`), archive only

Step Right Solutions is a historical source read through archived pages. This guide describes its indexes, round sheets, and remaining verification needs.

[All sources](README.md) · [Shared fetching rules](../fetching.md)

## 1. Status

Verified 2026-09-11 from Wayback captures
(`journal/evidence/collection/wayback-2026-09-11/`, `wayback_id_steprt_*.hdr`). The
origin returns empty 200 responses today (`journal/investigations/undated/initial-source-survey.md`). The
operator relationship is unknown; the company's pages are copyright
2007 to 2014. This source is read only from the Wayback Machine and is
never fetched from the origin.

## 2. What it gives

Per contest and round for US West Coast, Canadian, French, and
Singaporean events from 2009 to 2016, with a tail to 2024: bibs, names,
per-judge callback marks (`1`, `2`, `3`) with anonymous columns, totals,
finals with leader and follower names, per-judge placements, and final
placement. Judges are named per round, and the chief judge is named on
some pages, but marks are not attributable to a named judge. No WSDC
ids. The retained 507/508 control pair verifies `adv` classes against final
participants; a general promotion rule remains **unverified**.

The archive holds 163 event slugs, 108 of them with round pages, and
1,685 round pages. Per event year (slugs with round pages / round
pages): 2009: 3/42, 2010: 4/58, 2011: 5/100, 2012: 6/137, 2013: 15/261,
2014: 17/340, 2015: 14/349, 2016: 13/203, 2017: 6/47, 2018: 4/41,
2019: 4/29, then a handful a year.

## 3. URL patterns

```
http://steprightsolutions.com/events                     index: every series with city and a link per year
http://steprightsolutions.com/events/<slug>              event page: contests with Prelims, Semi-Finals, Finals links
http://steprightsolutions.com/events/<slug>/round/<id>   one round
```

Both `steprightsolutions.com` and `www.steprightsolutions.com`, with and
without `:80`, appear in the archive; the CDX `urlkey` unifies them.

## 4. Discovery

One CDX query per capture year (2013 to 2025) over the domain, HTML
only. The events index is parsed for series name, city, and per-year
slugs; the same navigation block is on every page, so any capture will
do. Event pages give dates (`December 2 - 5, 2010`) and the contest and
round list. Source event references are `steprightsolutions:<slug>`.

## 5. Change detection

None needed; captures are immutable.

## 6. Politeness settings

None; the origin is never fetched. Archive requests follow
`wayback-machine.md`.

## 7. Fetch procedure

Index capture, then event pages newest year first, then round pages,
all through the Wayback transport with the capture selection rule in
`docs/reference/backfill.md`.

## 8. Parsing

- `steprightsolutions.index`: `div.event` blocks with `h4` (series),
  `div.location`, and `div.dates a[href^=/events/]` (year and slug).
- `steprightsolutions.event`: breadcrumb `Home / Events / <name>`,
  a date line, then contest headings each followed by round links.
  Contest names are free text: `Novice West Coast Swing Jack & Jill`,
  `All-Stars / Champions West Coast Swing Jack and Jill`,
  `Master's Strictly Swing`, `NASDE Classic`, `Rising Star`.
- `steprightsolutions.round`: prelims have one table per role under
  headings `Leaders (n)` and `Followers (n)`, columns `BIB#`, `Name`,
  one unlabeled column per judge, `Total`; judges are listed above the
  table in a `Judges:` line and, when present, a `Chief Judge:` line.
  Finals have one table with `BIB`, `Leader`, `Follower`, per-judge
  placements, `Placement` as an ordinal (`1st`). Marks map `1` to
  `yes`, `2` to `alt`, `3` to `no` (`callback_legend = legacy_3`);
  the complete round-507 control contains only `1`, `2`, and `3`, with an
  explicit grouped-header legend. Sub-values elsewhere remain **unverified**. Marks reference
  `anon-<n>` judges; named judges are recorded on the round with
  `marks_attributed = false`.
- Bibs are zero-padded strings (`032`); keep as printed.

## 9. Quirks

- Round headings misspell `Semi-FInals` and `FInals` on some pages.
- Newcomer contests can be finals-only.
- All eight round-508 final bibs match leaders' round-507 bibs. Ownership for
  other finals remains **unverified**; a single-page parse does not assign the
  printed final bib to either partner.
- The 2013 index and the 2016 pages list different series sets; the
  union across captures is the event list.

## 10. Backfill

Everything here is backfill. Origin fallback: none.

## 11. Load estimate

About 1,900 archive requests once.

## 12. Operator switch

Not applicable to the origin. A request from the company or an event
director is honored like any removal request.

## 13. Open items

- A bounded promotion rule beyond the verified 507/508 control pair.
- Sub-values of mark `2` outside the retained preliminary control.
- Finals bib ownership outside the observed cross-page matches.
- A real results-bearing event page to control contest and round-link parsing.

## 14. Local implementation status

Offline parser preparation is present in `sources/steprightsolutions/`.
Index observations retain series, location, year, and original event
URL. Event observations retain raw dates and contest/round links.
Round observations retain zero-padded bibs, names, anonymous columns,
raw marks and placements, the printed panel roster, and chief judge.
Panel names do not need WSDC numbers and never own anonymous columns.

Complete approved bodies now control the index, a metadata-only event, and
preliminary/final rounds. Event and round extract/parser versions are 2.
The real grouped `Judge Scores *` and `Judge Placements *` headers expand into
anonymous columns only when neighboring headers and row widths match the
reviewed layout. Preliminary groups require the supported legend. Other merged
layouts reject. Source group labels, widths and attributes remain retained.
Judge notes are stored separately from the roster, so anonymity and private
chief-score statements never become judge names. The metadata-only event
requires the reviewed title/date header and original event URL. It emits
`round_listing_status = no_round_links` and a finding; neither complete
enumeration nor unavailable results follows.

The source emits no seed or discovered watches. Its source-specific
observations are not admitted to the canonical contest projector yet.
This prevents the current projector from inventing an alternate rank
for `2` or assigning a finals bib to both partners. Callback `2` means
an unranked alternate; unknown values are retained with a warning.
Promotion stays unknown even when a row has a highlight class. Finals
bib ownership stays unknown and produces a warning.

Tests in `tests/fixtures/sources/steprightsolutions/` include synthetic inputs
and four byte-exact approved archived bodies with provenance hashes. Offline
cross-page checks demonstrate all 16 `adv` participants in the final and all
eight final bibs matching leaders. These checks do not give a single-body
parser verified predecessor evidence or activate canonical ownership.
Malformed groups, unknown legends, empty pages, changed marks and changed judge
notes have dedicated controls. See the
[independent fixture review](../../../journal/investigations/2026/fixture-controls-review-2026-09-17.md)
and [implementation receipt](../../../journal/investigations/2026/stepright-real-controls-2026-09-17.md).
WP14 remains open: canonical integration, independent contract acceptance,
page-kind enforcement and historical event-year coverage are not complete.
