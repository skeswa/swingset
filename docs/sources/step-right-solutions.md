# Step Right Solutions (`steprightsolutions.com`), archive only

## 1. Status

Verified 2026-09-11 from Wayback captures
(`research/verification/2026-09-11/`, `wayback_id_steprt_*.hdr`). The
origin returns empty 200 responses today (`design/sources.md`). The
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
ids. Whether promotion is marked on prelims rows is **unverified**.

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
`design/backfill.md`.

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
  whether `2` carries sub-values is **unverified**. Marks reference
  `anon-<n>` judges; named judges are recorded on the round with
  `marks_attributed = false`.
- Bibs are zero-padded strings (`032`); keep as printed.

## 9. Quirks

- Round headings misspell `Semi-FInals` and `FInals` on some pages.
- Newcomer contests can be finals-only.
- Which bib a finals row shows for a Jack and Jill couple is
  **unverified**.
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

- Promotion marking on prelims rows.
- Sub-values of mark `2`.
- Finals bib ownership.

## 14. Local implementation status

Offline parser preparation is present in `sources/steprightsolutions/`.
Index observations retain series, location, year, and original event
URL. Event observations retain raw dates and contest/round links.
Round observations retain zero-padded bibs, names, anonymous columns,
raw marks and placements, the printed panel roster, and chief judge.
Panel names do not need WSDC numbers and never own anonymous columns.

The source emits no seed or discovered watches. Its source-specific
observations are not admitted to the canonical contest projector yet.
This prevents the current projector from inventing an alternate rank
for `2` or assigning a finals bib to both partners. Callback `2` means
an unranked alternate; unknown values are retained with a warning.
Promotion stays unknown even when a row has a highlight class. Finals
bib ownership stays unknown and produces a warning.

Tests use explicitly synthetic HTML in
`tests/fixtures/sources/steprightsolutions/`. The retained CDX index
supports URL discovery only. The retained index and round 507/508
response headers have no accompanying page bodies. These tests do not
close the three open source questions or demonstrate real event-year
coverage. WP14 acceptance remains open until complete archived bodies
are retained, reviewed, and tested; canonical integration follows that
review and the V5 admission gates.
