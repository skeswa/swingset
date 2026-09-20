# D-0175: Fewer records, receipts not bundles, and tiered review

Recorded: 2026-09-20  
Decided by: owner, 2026-09-20; source: "Please enact all of these suggestions", after the process review in the same session  
Topic: Process  
Supersedes: [D-0006](0006-record-every-decision.md) and [D-0169](0169-agents-decide-and-record-and-ask-only-for-high-impact-choices.md) on which choices get a record; [D-0105](0105-reduce-new-evidence-volume.md) on the evidence budget; [D-0126](0126-pause-v2-overnight-after-integrated-local-validation.md) on where work stops  
Superseded by: —

## Decision

Work is release work or maintenance. Release work names the dataset commit it
will improve. Maintenance gets one session a week; side quests go to the
queue in the plans index. `AGENTS.md` is the home of these rules:

- A decision record only for a choice a future reader could want to reverse.
  Routine choices go in the commit message. One screen per record. Plan
  direction is a plan edit.
- One compact receipt per production operation; at most 200 KiB of new
  evidence per change; rehearsal output, logs, packets and frozen scripts
  stay in scratch. Journal tools keep maintained tools only; a script used
  twice becomes a subcommand.
- `docs/status.md` is a dashboard of at most 60 lines. No transfer prompts;
  a session starts from `AGENTS.md` and status.
- Three review tiers: offline change, tests and format; production switch,
  preflight and post-check receipts; deleting, publishing or spending, one
  independent check with a written verdict. One reviewer per change;
  reviewers are not reviewed.
- Optimization starts from a measurement receipt and a plan trigger. For
  state size: database over 20 GB or worker disk under 100 GB free. The
  bounded-state plan is parked.
- One agent session per checkout, taken with `mise run session-start`.
- A new test goes in the existing file for its area; the core suite stays
  under three minutes.

## Why

Between 2026-09-17 and 2026-09-20 the repo gained 165 decision records, 117
investigations, about 500 MB of evidence and 35,000 lines of one-off tools,
while the dataset was not published once and collection was held the whole
time. Effort went to reviews of reviews, rehearsals retained as evidence, and
a storage plan chasing 2.9 percent of a 5 GB file on a disk with 443 GB free.
Transparency means a reader can learn what is published, running and next in
a minute. Volume worked against that. See the
[process review](../investigations/2026/process-review-2026-09-20.md).

## Links

- [AGENTS.md](../../AGENTS.md), the rules' one home
- [Status page as of 2026-09-18](../archive/status-2026-09-18.md), the last long-form status page
