# Monterey missing-data investigation — 2026-09-13

Monterey's detailed results exist. Production discovered all 33 scoring.dance
round pages but fetched none. A separate event-name mismatch would keep the
rounds apart from the WSDC registry event. Offline replay also reproduced a
Strictly partner-column defect; the working copy now corrects that defect.

## Recovered personal results

Sandile Keswa, WSDC 26781, bib 254, competed in two contests among the 33
listed round pages checked:

| Contest                  | Partner                                         | Result                                        | Source                                                                                                                                                                                                       |
| ------------------------ | ----------------------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Intermediate Jack & Jill | Kyra Peters, WSDC 23974                         | 11th; promoted from prelims and semifinals    | [Finals](https://scoring.dance/enUS/events/272/results/4900.html), [prelims](https://scoring.dance/enUS/events/272/results/4898.html), [semifinals](https://scoring.dance/enUS/events/272/results/4899.html) |
| Intermediate Strictly    | Lynne Yun (printed `Lynne yun`; no WSDC number) | Eliminated in prelims; five No votes, score 0 | [Prelims](https://scoring.dance/enUS/events/272/results/4914.html)                                                                                                                                           |

The published registry records both J&J partners as Intermediate finalists with
one point each at Monterey in January 2026. The Strictly entry is additional
to the earlier 40-contest personal inventory: source research brings that
inventory to 41 contests across the same 20 event editions. This is not yet a
change to the published dataset.

## Why production is missing it

The inspected public baseline is Hugging Face commit
`81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`.

1. **Unfetched rounds.** Production captured the event index on September 10
   at 06:02 UTC and successfully parsed it. All 33 child watches have null
   `last_checked_at`, null body hashes and `ever_ok=0`. There are no round
   snapshots, so there is no Monterey round parsing failure to retry.
   The parent was checked again on September 12 and is now archived.
2. **Acquisition backlog.** There are 3,757 scoring.dance round watches that
   have never been checked. The host reached its 800-request daily budget
   on September 10 and September 12. Scheduled workers are currently held
   by the operator marker described in `docs/v2-resume.md`. These facts
   explain the operational setting; they do not prove the exact historical
   scheduling decision for each Monterey watch. The host has no automatic
   pause, and `operator_pauses` is empty.
3. **Split event identity.** `scoringdance:272` maps to
   `2026-01-monterey-swinfest`, using the source's misspelling. The registry
   event is `2026-01-monterey-swing-fest`, series `wsdc-62`. The working-copy
   event alias now maps this specific source edition to the registry event.
4. **Dropped Strictly partners.** The round extractor labels the second
   competitor column only for finals. Strictly prelims also put both
   partners on one row. Lynne has no printed WSDC ID, so the projector
   cannot recover her unlabeled column through its ID-column fallback.
   Before the correction, offline projection kept Sandile's entry and
   elimination but discarded Lynne's name. The correction labels the blank
   follower column on Strictly couple sheets, leaving titled judge columns
   alone. Extractor and parser versions advance from 3 to 4.

The exact event end date remains unresolved. Retained research lists January
15–18 on scoring.dance and January 15–19 on the calendar. The production
source event has January 15 for both dates. None of those records alone
establishes the complete festival schedule.

## Verification and repair status

This investigation fetched robots.txt and the 33 already-discovered round
URLs, sequentially, with at least five seconds between requests and the
project User-Agent. Every response was HTTP 200. No source registry profile
URLs were requested. Saved research responses are in
`tmp/monterey-investigation/live/`; their `.hdr`, `.meta`, and `.body` files
retain response metadata and bodies. Production collection state was read only.

The current parser processed all 33 pages without parse warnings. With the
explicit event mapping, offline projection produced 16 contests, 33 rounds,
990 contest entries, and 163 placements. Entry counts are participations,
not distinct people. Sandile's two entries retain Kyra and Lynne, respectively,
and the expected placement and callback outcomes. This was a projection check,
not a complete publication or identity-linking acceptance run.

The added regression test failed on the old extractor because Sandile's
partner was null, then passed after the correction. The focused source,
projection, pipeline, and unsupported-judge suite passed 37 tests.

The source correction and edition alias are local hand edits. They have not
been deployed or published. Completing the production repair requires normal
acquisition of the queued rounds, acceptance of the alias, replay under the
new extractor, identity linking, and publication through the existing release
process. The operator hold remains in place.

## Evidence

- [Production state query](verification/2026-09-13/monterey/production-evidence.json)
- [Monterey watch inventory](verification/2026-09-13/monterey/watches.json)
- [Offline replay results and personal source rows](verification/2026-09-13/monterey/replay-report.json)
- Offline replay driver: `tmp/monterey-investigation/replay.py`. It reads the
  saved bodies, creates a temporary database, and runs `project_event` without
  making requests. Replay provenance timestamps are synthetic; response
  timestamps belong to the saved fetch metadata.
