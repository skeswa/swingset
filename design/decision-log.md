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
