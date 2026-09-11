# WSDC points registry (`points.worldsdc.com`)

## 1. Status

General facts verified 2026-09-04 in `design/sources.md`; found and missing
lookup fixtures re-checked on 2026-09-09 UTC. The registry is the identity source for the whole dataset.
No personal relationship. `robots.txt` allows everything; no terms
page; no API key.

## 2. What it gives

One dancer per lookup: WSDC number, name, primary role, level, and
every pointed placement (division, event series id and name, month,
result, points). No prelims, no bibs, no field sizes.

## 3. URL patterns

```
POST https://points.worldsdc.com/lookup2020/find      num=<wsdc_id>
POST https://points.worldsdc.com/lookup/find          num=<wsdc_id> | q=<name>   (legacy; name search unused)
```

## 4. Discovery

Ids are dense integers from 1 to about 29,000. Bootstrap continues through the
highest ID in the archived comparison dump or any locally verified found
lookup, whichever is greater. Only 20 consecutive verified misses above that
bound complete bootstrap. Weekly probes use the same 20-miss rule above the
highest projected dancer after bootstrap.

## 5. Change detection

None possible: POST, no validators, no bulk export. Every refresh is a
full fetch of a few KB. The schedule keeps refreshes rare (section 7).

## 6. Politeness settings

```toml
[hosts."points.worldsdc.com"]
min_gap_seconds = 2                # responses are tiny and the site exists for lookups
daily_request_budget = 1500        # 20000 during the one-time bootstrap sweep
```

## 7. Fetch procedure

1. Bootstrap once: ids 1..N at one request per 2 s (about 16 hours),
   cross-checked against the `wsdc.mechstack.dev/data.json` dump, which
   is downloaded once and never written into the dataset. The dump's maximum
   dancer ID is cached with its archive hash, so each lookup does not reread the
   dump. Locally verified found observations extend the bound even when dancer
   projection is deferred.
2. Post-event confirmation: each finalist we identified is refreshed
   once a day until the event appears in their record or 30 days pass.
3. Trickle: dancers not refreshed in 365 days, at most 100 a day.
4. Never call autocomplete or name search from the pipeline.

## 8. Parsing

`wsdc_registry.dancer`: JSON shape in `design/sources.md`. Quirks:
`placements` is `{}` or `[]`; `dancer.id` is not the WSDC number
(`wscid` is); names are transliterated English.

The 2026-09-09 captures use `West Coast Swing` for style,
`Primary Role Leader` / `Primary Role Follower`, and division codes
`NEW`, `NOV`, `INT`, `ADV`, `ALS`, `CHMP`, `JRS`, `SPH`, and `MSTR`.
Projection maps the verified labels to canonical values. Registry age categories
remain `juniors`, `sophisticated`, and `masters`; mapping all three to `none`
would collapse distinct placements in the primary key. Unknown division records
remain raw and open a finding instead of being assigned a guessed category.

Parser version 3 also retains the highest-level and point fields for the
dominant and non-dominant roles. Projection assigns them to leader/follower
using the printed primary role. Existing observations are reparsed and rebuilt
when parser or projector versions change.

## 9. Quirks

Duplicate numbers are merged by hand by WSDC staff. Results land 1 to 7
days after an event.

A missing numeric id returns HTTP 404 with an HTML error document. This was
verified on 2026-09-09 UTC with ids 1000000 and 1000001; both bodies had SHA-256
`8437bd0ef46a19c9a7c294c53e0429b40e76ebbd5fe9fd73a9025752495ddb1c`.
Only that exact status-and-body pair is a `NotFound` lookup. Any changed 404
body is `Invalid`, so a new server error page cannot advance the sweep.

## 10. Backfill

The bootstrap sweep is the backfill.
Interior gaps do not contribute to its terminating miss count. Reseeding clears
stale cross-check completion and weekly-probe bookkeeping before starting from
the requested ID.

## 11. Load estimate

About 1,000 requests a day in the weeks after busy weekends, under 100
otherwise, each a few KB.

## 12. Operator switch

`User-agent: swingset` in `robots.txt`, or an issue.

## 13. Open items

From `design/open-questions.md`: meaning of `adv_sliding` and `as_sliding`, whether merged numbers are
retired or redirected.

## First-pass corrections (2026-09-10)

`Advance` is normalized to `advanced`. Unrecognized dancer levels are `unknown`,
not `none`. `PRO` and `TCH` remain literal registry division values with
unverified meanings. Conflicting results or points at one registry placement
key are withheld with a conflict finding; identical duplicates coalesce.

Registry series IDs remain `wsdc-*`. A placement receives an `event_id` only
when its normalized series name and month select one canonical event. Ambiguous
matches remain null and appear in review. Points checks use the mapped event,
role, division, style, and result, with separate preliminary role field sizes.
