# WSDC points registry (`points.worldsdc.com`)

## 1. Status

Facts verified 2026-09-04 in `design/sources.md`; nothing re-checked on
2026-09-08. The registry is the identity source for the whole dataset.
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

Ids are dense integers from 1 to about 29,000. Weekly probe above the
highest known id until 20 consecutive misses.

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
   is downloaded once and never written into the dataset.
2. Post-event confirmation: each finalist we identified is refreshed
   once a day until the event appears in their record or 30 days pass.
3. Trickle: dancers not refreshed in 365 days, at most 100 a day.
4. Never call autocomplete or name search from the pipeline.

## 8. Parsing

`wsdc_registry.dancer`: JSON shape in `design/sources.md`. Quirks:
`placements` is `{}` or `[]`; `dancer.id` is not the WSDC number
(`wscid` is); names are transliterated English.

## 9. Quirks

Duplicate numbers are merged by hand by WSDC staff. Results land 1 to 7
days after an event.

## 10. Backfill

The bootstrap sweep is the backfill.

## 11. Load estimate

About 1,000 requests a day in the weeks after busy weekends, under 100
otherwise, each a few KB.

## 12. Operator switch

`User-agent: swingset` in `robots.txt`, or an issue.

## 13. Open items

From `design/open-questions.md`: response for a non-existent id,
meaning of `adv_sliding` and `as_sliding`, whether merged numbers are
retired or redirected.
