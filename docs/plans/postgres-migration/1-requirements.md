# 1. Requirements and non-requirements

Read [the summary](README.md) first; next is [2. Schema](2-schema.md). This page says what the move must
achieve, what it must never break, and what it deliberately does not do.
Every later page serves these lists. Decisions:
[D-0263](../../../journal/decisions/0263-postgres-holds-all-state-and-moves-in-four-stages.md)
(Postgres holds everything, four stages) and
[D-0264](../../../journal/decisions/0264-declared-transform-versions-and-differential-proofs-decide-re-derivation.md)
(proofs instead of re-derivation, stage 0).

## What does not change

The pipeline itself: sources, parsers, contracts, the five loops, the
release gate, independent checks before publishing, the fetching rules and
budgets, and the decision records. The move changes where state lives and
what runs it.

## What the owner asked for (2026-10-19)

In the owner's words, lightly edited for spelling:

> Postgres should maintain all of our state, including overrides,
> exceptions, approvals etc. It should be maximally queryable because I want
> to build on top of it and expose it as an API at some point. We should
> still be able to do full-fidelity data exports to Hugging Face. First I
> want to refactor locally and verify that everything still works, then
> "cloud-ify" assets and resources, then do a dry run on Dokploy, then
> switch over to Dokploy full time.

Later: daily publishing is not a requirement, because this move lets the
data be reached through Postgres (and later an API) rather than only
through Hugging Face; and the scraping and safety rules must be
configurable and changeable inside Postgres.

Later the same day: re-derivation is very costly, so reduce it, as a stage 0;
and the whole move should be able to run in one session, such as a weekend.
The owner then agreed that full-size checks run on the server, because the
Mac lacks the disk, and chose the lighter plan shape ("B"): no production
change before the switch, one planned re-derivation in the dry run as the
proof, and a thin stage 0. Moving files into object storage comes after
the switch, so the order is now port, dry run, switch, then the cloud
files.

## Requirements

| #   | The move must…                                                                                                                                                                                    | Checked by                                                                        |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| R1  | keep **all** state in one Postgres database: pipeline data and every decision (overrides, exceptions, approvals, holds, settings)                                                                 | [2. Schema](2-schema.md); after-switch steps                                      |
| R2  | record every decision as a row with who proposed it, who approved it, when, and why; never edit one in place                                                                                      | `gov` tables and triggers                                                         |
| R3  | be the main way to reach the data: queryable, with stable ids, release history and redirects, so an API can replace Hugging Face as the primary way people get the data                           | `catalog` namespace                                                               |
| R4  | keep full-fidelity exports to Hugging Face possible, byte-for-byte as complete as today; the release builder stays their only writer                                                              | the dry run's release comparison; the release gate                                |
| R5  | keep every hash the code computes unchanged, unless a planned switch changes it with receipts                                                                                                     | golden-hash tests (stage 1)                                                       |
| R6  | never re-derive production because code bytes or the runtime changed; re-run only a transform whose declared version is bumped                                                                    | stage 0; the dry run's one planned re-derivation is on the server, not production |
| R7  | make the scraping and safety rules data: every rule below is an approved row in `gov` that can be read, queried and changed without a deploy                                                      | [2. Schema](2-schema.md#scraping-and-safety-rules-as-rows)                        |
| R8  | move all backups off the machine at the switch, and large unchanging files after it, with a second copy at a second provider                                                                      | the dry run; after the switch                                                     |
| R9  | back up everything that cannot be rebuilt (the irreplaceable tables and the files) off the machine twice a week, and be able to rebuild the rest from it                                          | each backup's check; the full rebuild drill after the switch                      |
| R10 | stop depending on the owner's Mac being awake                                                                                                                                                     | the switch                                                                        |
| R11 | keep today's scraping rules as the starting values: one fetcher, one request in flight per host, a 2-second floor, robots.txt and its `Crawl-delay`, conditional requests, the project User-Agent | the dry run cannot fetch; [fetching rules](../../reference/fetching.md)           |
| R12 | keep the repo's safety rules as the starting values: a hold with owner and expiry, receipts for production changes, an independent check before deleting data, publishing or spending money       | the review table on each stage page                                               |
| R13 | change nothing in production before the switch, and keep the switch reversible until the server's first fetch                                                                                     | each stage's rollback section                                                     |

## Non-requirements

The move deliberately does **not**:

| Not doing                                               | Why                                                                                                                                                                                                          |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| build a public API                                      | separate work, decided by the owner once the catalog has filled; at the switch the owner queries with SQL over an SSH tunnel                                                                                 |
| change the published dataset's format, columns or files | R4; a format change is a planned release change, not part of this move                                                                                                                                       |
| improve types, split or rename tables during the move   | every table moves as it is ([2. Schema](2-schema.md#the-rules-for-the-move)); cleanup comes after                                                                                                            |
| allow several writers at once                           | one write transaction at a time, as today; loops still run side by side ([3. Database code](3-database.md#locks))                                                                                            |
| use an ORM                                              | today's code is plain SQL (1,787 calls); it stays plain SQL                                                                                                                                                  |
| use Dokploy's own cron or a connection pooler           | Dokploy cron has no overlap lock or timeout; poolers break session locks                                                                                                                                     |
| run two fetchers, even for the dry run                  | it would break the request limits promised to each site (R11)                                                                                                                                                |
| copy data back from Postgres into SQLite                | rollback after the server's first fetch is forward only, by restoring a dump                                                                                                                                 |
| keep publishing daily, or on any fixed cadence          | publishing is a setting in `gov`, not a requirement; Hugging Face is one export, and an API will become the main way to reach the data                                                                       |
| move resource tuning into `gov`                         | memory limits, batch and pack sizes change no output and no politeness; they stay in config files deployed with the code                                                                                     |
| back up the rebuildable tables                          | about 37 GB of caches that re-derivation rebuilds; published releases already live on Hugging Face                                                                                                           |
| publish private decisions                               | suppressions, identity decisions, authors and notes are never published                                                                                                                                      |
| activate new sources or contracts during the move       | calendar contract, danceconvention.net and SwingDirector wait until after the switch unless the owner says otherwise                                                                                         |
| run SQLite and Postgres side by side                    | the port is Postgres-only on its own bookmark; SQLite stays only as the importer's checkpoint reader                                                                                                         |
| build a full proof system before the move               | comparing side effects, cross-runtime proofs and deploy gating under a hold are deferred ([5. Stage 0](5-stage-0.md#deferred-unless-a-measurement-says-otherwise)); the dry run's re-derivation is the proof |
| keep the 2026-09-14 transfer plan                       | it was written for schema 14 and a one-shot transfer; this plan replaces it                                                                                                                                  |

## Numbers that define done

| Measure                                              | Target                                                                                                      | Today                                                                        |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| full re-derivations of production caused by the move | 0 (one on purpose, on the server, in the dry run)                                                           | the first design needed 3, 10 to 14 hours each                               |
| release gap at the switch                            | none required; measured in the dry run                                                                      | a re-derivation at the switch: 10 to 14 hours of work                        |
| recovery point                                       | up to about 3½ days (backups twice a week)                                                                  | a manual copy to the Mac                                                     |
| recovery time                                        | about a day: restore, then a full re-derivation (estimate)                                                  | not measured                                                                 |
| copies of every stored file                          | 2 providers, after the switch                                                                               | 1 disk                                                                       |
| a whole-graph verify run                             | under 1 hour                                                                                                | about 35 minutes at 2 processes on the worker (estimate from measured times) |
| the server's first run at gate 3                     | every loop three clean ticks in a row; one automatic release published; the first backup and its check pass | —                                                                            |

## Owner decisions still open

Make all of these before starting; the plan has no waiting periods in
which to make them.

| Decision                            | Needed before           | Recommendation                      |
| ----------------------------------- | ----------------------- | ----------------------------------- |
| publish allowlisted decision tables | after the switch        | yes, without author or note columns |
| build the API                       | after the catalog fills | separate work                       |
