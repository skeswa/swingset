# Decision log

Decisions made in the design review on 2026-09-04. Each one is already
reflected in the other design documents; this table is the index.

| # | Decision | Where |
|---|---|---|
| 1 | Runs on a Linux/NixOS box the owner already operates, not GitHub Actions | [operations](operations.md#host) |
| 2 | Packaged as a nix devshell plus uv; NixOS module owns timers and state dir | [technology](technology.md), [operations](operations.md#host) |
| 3 | Private HF archive repo is a backup only: daily on weekdays, three times daily on weekends, only when changed | [operations](operations.md#backup-and-restore) |
| 4 | Registry bootstrap is a full sweep at 1 request per 2 s, cross-checked against the mechstack dump | [sources](sources.md#wsdc-registry-pointsworldsdccom) |
| 5 | Full historical backfill of every platform archive, newest first, lowest priority | [scheduling](scheduling.md#watch-states-and-intervals) |
| 6 | Every contest on a results page is kept and labeled; unsupported layouts are visible rows | [parsing](parsing.md#parsing-rules), [data model](data-model.md#tables) |
| 7 | WSDC number is the only identity; no swingset person id | [overview](overview.md#non-goals-for-now), [identity linking](identity-linking.md) |
| 8 | `wsdc_id` filled for `confirmed` and `probable`; `link_candidates` keeps every scored candidate | [data model](data-model.md#tables), [identity linking](identity-linking.md#link-status) |
| 9 | Review via a published `review_queue` and CSV overrides in git | [data model](data-model.md#tables), [operations](operations.md#observability) |
| 10 | Tables hold current belief; history via Hub commits and `changelog` | [identity linking](identity-linking.md#retroactive-correction), [publishing](publishing.md#commit-strategy) |
| 11 | Dataset license ODC-By 1.0; code stays MIT | [publishing](publishing.md#dataset-card) |
| 12 | Suppression nulls identity and keeps structure | [build](build.md), [ethics and legal](ethics-and-legal.md) |
| 13 | Live polling floor 15 min now; per-source adaptive floor later, bounded 5 to 30 min | [scheduling](scheduling.md#watch-states-and-intervals), [open questions](open-questions.md) |
| 14 | Breaking schema changes in place with `schema-v<N>` tags; ids and enums are public API | [data model](data-model.md#identifiers), [publishing](publishing.md#commit-strategy) |
| 15 | `event_id` is `<yyyy-mm>-<series slug>` using the end date's month | [data model](data-model.md#identifiers) |
| 16 | No derived points columns; `points_matches_expected` only after the registry posts | [data model](data-model.md#tables), [WSDC rules](wsdc-rules.md) |
| 17 | Judges are named and linked, with the same suppression path | [data model](data-model.md#tables), [identity linking](identity-linking.md#retroactive-correction) |
| 18 | DCN payload evaluated by a sandboxed `node` subprocess | [parsing](parsing.md#parsing-rules) |
| 19 | Dataset lives at `skeswa/swingset` | [publishing](publishing.md#repos) |
| 20 | Contact channel is GitHub issues with templates; no email in the User-Agent | [fetching](fetching.md#identity), [ethics and legal](ethics-and-legal.md) |

Decisions from the scraping research on 2026-09-08:

| # | Decision | Where |
|---|---|---|
| 21 | Plain httpx with a hand-written per-host gate; no Scrapy, crawlee, or hishel | `research/scraping-techniques.md` |
| 22 | Backfill reads the Wayback Machine first; the origin only for gaps | [fetching](fetching.md#archive), [scraping plan](scraping-plan.md) |
| 23 | The cheapest resource per event carries the fast timer (EEPro autoindex, WDR JSON, scoring.dance event page, DCN results tab); children are fetched on parent change plus a slow clock of their own, set per host in the playbook, because no parent signal is exact enough to reveal every correction | [scheduling](scheduling.md#watches) |
| 24 | Bodies unchanged by fingerprint are discarded, not archived | [fetching](fetching.md#change-detection) |
| 25 | `Accept-Encoding: gzip` only; per-host byte budgets; `User-agent: swingset` robots group is the operators' switch; defaults live in fetching.md and overrides only in a playbook's section 6 | [fetching](fetching.md#politeness-rules) |
| 26 | World Dance Registry is a first-class source (M3b); long tail is overrides plus generic adapters, never per-event parsers | [sources](sources.md#world-dance-registry-pro-score-scoresworlddanceregistrycom), `docs/sources/long-tail.md` |
| 27 | Local LLM extraction is a manual draft tool feeding the review queue, never a pipeline stage | `docs/sources/long-tail.md` |
| 28 | DCN gets a 10 s gap, 30 min live floor, and a 300 MB/day byte budget until a lighter endpoint is found | `docs/sources/danceconvention.md` |


Decisions from the implementation-plan maintainability review on 2026-09-08:

| # | Decision | Why and owner |
|---|---|---|
| 29 | Watches own source observations; canonical rows are scope projections | Re-parsing and alias corrections replace evidence coherently; [architecture](architecture.md#observations-and-projections) |
| 30 | Changed inputs transactionally enqueue durable units; queues alone define unfinished parse, project, and link work | Removes competing stage fingerprints and dirty-scope gates; conservative all-event linking catches new registry candidates; [local state](state.md#invalidation) |
| 31 | Build depends directly on all published inputs | Canonical corrections must publish even when identity links stay the same; [build](build.md#build-inputs) |
| 32 | Candidate reuse is keyed by build inputs and baseline; publication markers are the sole journal | Changelog depends on its parent, and a database completion row cannot prove files exist; [publishing](publishing.md#candidate-and-baseline) |
| 33 | Stable content comparison is separate from exact manifest identity | Retry metadata must not create public versions; [build](build.md#immutable-contents) |
| 34 | Backup is a verified artifact closure, including extracts, captured inputs, and pending publication intent | SQLite alone cannot recover files or remote acknowledgment; restore holds publication when the remote is ahead of recoverable private state; [operations](operations.md#backup-and-restore) |
| 35 | Only duplicate scheduled cycles may skip a held lock successfully | Manual commands must apply or report failure; backups must eventually run; [operations](operations.md#locks-and-operator-commands) |
| 36 | Classify responses before changing automatic host pauses | Expected WDR unavailability must not pause unrelated watches; [fetching](fetching.md#response-classification) |
| 37 | Store findings as evidence; compute the review queue | Avoid two mutable versions of the same review state; [architecture](architecture.md#findings-and-review) |
| 38 | Design contracts move to their owners immediately; the work plan owns sequencing and acceptance | Removes conflicting specifications and the deferred WP10 documentation merge; [design index](README.md) |
