# Prior art: registry scrapes and analyses

Read 2026-09-11 from the sources the owner supplied. These are four
independent efforts, 2018 to 2025, that scraped the WSDC points registry
and analysed division progression. None of them touches score sheets,
prelims, or non-pointing entrants; all of them are limited to what the
registry says about finalists who earned points. That limitation is the
gap `swingset` fills, and their questions are the questions the dataset
should make easy to answer. Findings are grouped by what they teach us
about the registry, about method, and about demand.

## The four sources

| Source                                                                                                                                                                                             | Author, date                              | What it is                                                                                                                           |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| [WSDC Project Part 1: 2018 Rule Change](https://conniedoesdata.com/2018/01/03/WSDC-Project-Part-1/) and [Part 2: Taking Some Requests](https://conniedoesdata.com/2018/03/17/WSDC-Project-Part-2/) | Connie Wang, January and March 2018       | Two blog posts with R and Tableau analysis; code and CSVs at [conniewang3/WSDC-Project](https://github.com/conniewang3/WSDC-Project) |
| [dgarwin/westiestats](https://github.com/dgarwin/westiestats/blob/master/analysis/basic.ipynb)                                                                                                     | March 2020                                | Node scraper plus a Jupyter notebook: cohort level counts, time between first points per division, a toy All-Star classifier         |
| [tomtseng/wsdc-points](https://github.com/tomtseng/wsdc-points)                                                                                                                                    | Tom Tseng, April to May 2024              | Python scraper (`scrape-data.py`) and notebook (`get-stats.ipynb`)                                                                   |
| [How long it takes to move up divisions](https://modernswing.forum/posts/8CtcYhkf4Eo4NPH6r/how-long-it-takes-to-move-up-divisions-1)                                                               | Tom Tseng, 2025-01-12, Modern Swing Forum | Write-up of the notebook above; cites the other two as prior work                                                                    |

## What they teach about the registry

**Endpoints and response shape drift.** Each generation hit a different
shape of the same lookup:

| When                                 | Endpoint                                             | Form field                                        | Shape used by the code                                                                                                                                                               |
| ------------------------------------ | ---------------------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 2018 (Wang)                          | `POST /lookup/find`                                  | `q=<wsdc_id>` (a number in the name-search field) | `dancer.wscid`, `placements["West Coast Swing"]` as a **list** of division entries, each with `division.name` ("Novice", "All-Stars", "Masters") and `competitions[]`                |
| 2020 (westiestats)                   | `POST /lookup/find` with a JSON body `{num, _token}` | `num` plus a CSRF `_token` taken from the page    | `placements["West Coast Swing"]` as a **dict** keyed by division code (`NEW`, `NOV`, `INT`, `ADV`, `ALS`), `level.required`, `level.allowed`, `dancer.wscid`; a 404 for a missing id |
| 2024 (Tseng)                         | `POST /lookup2020/find`                              | `num=<wsdc_id>`                                   | `dancer_wsdcid`, `dominate_data.level.allowed`, `dominate_data.placements["West Coast Swing"][<code>].competitions[]`                                                                |
| 2026 (swingset, `design/sources.md`) | `POST /lookup2020/find`                              | `num=<wsdc_id>`                                   | `leader` and `follower` blocks, `dominate_role`, `dominate_required`, `dominate_allowed`; the 2024 `dominate_data` key is not in our verified sample                                 |

The registry has changed its JSON at least three times in eight years
without notice. This is why the parser is versioned and why every
response body is archived: a shape change must be a parser bump against
stored bodies, never a lost sweep. The `_token` requirement seen in 2020
is gone in 2024 and 2026.

**Quirks every one of them hit, which we already handle or should:**

- `placements` is `[]` when empty and a dict otherwise (Wang 2018,
  Tseng 2024). Dancer 3 has no placements; dancer 5 has level `PRO`
  (Tseng). Both are good fixture ids for the missing-placements and
  unknown-level paths.
- `competitions[]` is ordered newest first: all three scrapers take
  `competitions[0]` as the latest event and `competitions[-1]` as the
  first point. We should verify this ordering from the sweep rather
  than assume it, and not depend on it: sort by parsed month.
- Events can be recorded out of chronological order, and a combined
  Advanced/All-Star Jack and Jill is recorded as Advanced (Wang, part
  2). Wang also saw negative times from first Novice to first
  Intermediate point and attributed them to combined Novice/Intermediate
  contests recorded as Novice. This matches our rule that combined
  divisions award the lower division's points (`design/wsdc-rules.md`)
  and means `contests.combined_from` is the only way a consumer can
  tell a real Advanced result from a combined one. The registry cannot.
- Dates are month strings (`"January 2020"`); every analysis is at
  month granularity and says so. Exact dates are a swingset advantage.
- Masters points do not prove age eligibility; many eligible dancers
  never enter Masters (Wang).
- No residence data exists. Wang derived a region per dancer from the
  locations of their pointed events (US West Coast, Midwest plus Texas,
  East Coast plus most of Canada, Europe, Other for Singapore, Korea,
  Australia, New Zealand, Brazil) with thresholds to catch travellers.
  Any regional analysis on our data needs the same derivation, which
  needs clean `events.country` and `region`.

**Growth of the id space, from their hard-coded maxima:**

| Date       | Highest id   | Source                                           |
| ---------- | ------------ | ------------------------------------------------ |
| 2018-01    | about 16,800 | Wang, `range(16802)`                             |
| 2018-02-02 | 16,981       | Wang, part 2                                     |
| 2024-04-28 | 23,454       | Tseng, "manual trial and error"                  |
| 2026-09-09 | 27,039       | mechstack dump (`docs/implementation-status.md`) |

About 1,000 new numbers a year before 2020, about 1,200 a year since 2022. This supports the sweep budget in `docs/sources/wsdc-registry.md`
and the expectation that new-id probes find a few numbers a week.

## What they teach about method

- **Rates.** Tseng ran 4 to 5 requests per second single-threaded and
  suggests parallel requests; westiestats slept 400 ms between requests;
  Wang does not say. None reported being blocked. Our 2 s gap is 8 to 10
  times gentler than the fastest of these, and 5 times gentler than the
  slowest. `design/sources.md` already notes that scrapers at 3 per
  second are not blocked; this is further evidence, not a reason to
  speed up.
- **Missing ids.** Tseng found the maximum by typing numbers until they
  stopped resolving; westiestats stopped at the first null; Wang caught
  `ValueError` on non-JSON. None distinguished a gap from the end. Our
  rule (20 consecutive verified misses above the highest known id, exact
  404 body match) is the answer to the problem they all worked around.
- **Resumability.** Tseng checkpointed every 100 ids to a pickle;
  westiestats wrote 500-id JSON chunks and resumed from the largest file
  name. Our cursor table does the same durably.
- **Level derivation.** Wang computed the current division from points
  with the 2018 thresholds (15 then 16 Novice, 30 Intermediate) and
  treated All-Star as opt-in, counting only dancers with All-Star points
  in the last three years. Tseng used `level.allowed` from the response
  and computed "finish time" per division as the month cumulative points
  crossed the threshold (`NOV` 16, `INT` 30, `ADV` 60, `ALS` 150) or the
  month of the first point in the next division, whichever is earlier.
  westiestats used the composite `required-allowed` pair from the
  response. We publish the registry's own `level` fields and never
  derive; a consumer wanting Tseng's "finish time" needs per-placement
  points and months, which `registry_placements` has.
- **Survivorship.** Tseng states it plainly: the novice-to-all-star
  distribution only includes dancers who reached All-Star. Every "time
  to move up" statistic from the registry has this bias, and only a
  dataset with non-pointing entrants (prelims sheets) can measure who
  never moved up.

## What they teach about demand

The same questions recur across seven years:

1. Time between first points in consecutive divisions, by cohort and
   role (all three). Tseng's 2024 medians: Newcomer to Novice 1.0 years,
   Novice to Intermediate 1.42, Intermediate to Advanced 1.67, Advanced
   to All-Star 2.33, All-Star to Champion 2.08, Novice to All-Star 4.67
   (864 dancers). westiestats' 2020 medians agree within a few months.
2. How many dancers stall in a division and for how long (Wang: 39.6% of
   Intermediate and 34.1% of Advanced dancers past the 75th percentile
   of time in division, 2018).
3. Effects of rule changes (Wang: 76 All-Stars demoted and 157 dancers
   sent back to Novice by the 2018 rules; regional differences tested
   with Fisher's exact test).
4. Regional and international growth (Wang: international share rose
   from under 30% before 2014 to 38% in 2018).
5. Who moved up fastest (Tseng and westiestats both name individuals;
   fastest Novice to All-Star 8 months).
6. Masters as a division whose median competitor is Intermediate level
   and whose top 5% hold 38% of the points (Wang).

Every one of these was answered from a one-off scrape that went stale
the day it ran. A maintained dataset with `registry_placements` at
month precision, `events` with dates and countries, and `entries` for
everyone who danced makes each of them a query, and adds the two things
none of them could see: field sizes and the people who never pointed.

## Implications for swingset

- Verify and record the `competitions[]` ordering and the current shape
  of `dominate_*` fields during the bootstrap sweep; add dancers 3 and 5
  as fixtures if their quirks persist.
- Keep `contests.combined_from` populated wherever a sheet names a
  combined division; it is the only correction for the registry's
  lower-division recording.
- Consider a derived `dancer_milestones` table (first point per division
  per role, month; month the registry level changed) as a v1.1 addition.
  It is what all four efforts computed by hand and is cheap to build from
  `registry_placements`. It does not conflict with the no-derived-points
  rule if it carries months, not points. Recorded as an open question.
- Add the rule-change history these posts document to
  `design/wsdc-rules.md` so consumers can split cohorts at the right
  months: Novice requirement 20 to 15 points (Wang says 2012 in one
  place and 2013 in a revision note; **unverified** which), 15 to 16
  allowed and 30 required in 2018, All-Star eligibility rewritten in
  2018 (45 Advanced points in three years or 3 All-Star points in three
  years, replacing "1 All-Star point ever").
- The consumer examples in the dataset card should include one of these
  questions (time from first Novice to first Intermediate point by
  cohort) as a worked DuckDB query, since it is the question people
  actually ask.
