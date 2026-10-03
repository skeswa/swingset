# D-0236: Origin candidates and the event-site review are offline reports

Recorded: 2026-10-03  
Decided by: agent, 2026-10-03, under D-0172; source: architecture plan section 8, phase 5 (owner-adopted 2026-09-25)  
Topic: Historical backfill  
Supersedes: narrows WP16's online origin dispatch ([historical sources](../../docs/plans/historical-sources.md)); no earlier record held it  
Superseded by: —

## Decision

The loops make no origin request from the history plan. The offers pass,
`plan_pages` and `dispatch_one` offer archive captures only and never call
`origin_dispatch.candidate` or `schedule`. Origin candidates are an offline
report (`journal/tools/collection/origin_candidates.py`, on a state copy)
that a reviewer turns into a reviewed `overrides/source_urls.csv` row. The
event-site review (phase 5 WP2) moved the same way, to
`journal/tools/collection/event_site_review.py`.

`history/origin_dispatch.py` stays byte-identical, because parse code calls
its `managed()`; it is deleted at the next whole re-parse. Until then its
request gate, cadence receipt, the eligibility refusal and the fairness skip
still fail closed for any `history_origin_intents` row.

Published changes. A page whose archive offer stopped at
`capture_gap_exhausted` or `operator_pause`, and which the fallback then
marked `parent_document_pending`, no longer opens the "waits for its
admitted parent" `history_archive_gap` finding. An exhausted page keeps the
archive branch's finding ("exhausted", or the plan's "no retained archive
capture"); a paused one opens none until its offer runs. The 09-29
production copy has none open, and 0 intent, request or operator rows.
Migration 37 (part 3) closes the 230 open `history_event_site_review`
findings (`out_of_scope`, naming the tool) and sets their support inactive:
they lost their only writer and closer, so they leave `review_queue` and the
card's count on the next build.

## Why

The fallback never sent a request in production, yet `candidate()` ran in
every offers pass, and its projection question stalled collection from
2026-09-29 to 10-01 (D-0195). An origin request for a history page is rare
and needs a human check of the archive proof anyway.

## Links

- [Architecture plan, phase 5](../../docs/plans/architecture.md)
