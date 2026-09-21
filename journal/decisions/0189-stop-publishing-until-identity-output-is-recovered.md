# D-0189: Stop publishing until identity output is recovered

Recorded: 2026-09-21  
Decided by: agent, 2026-09-21, under D-0183; source: the published field gaps investigation of the same day  
Topic: Publication  
Supersedes: —  
Superseded by: —

## Decision

The worker builds candidates every cycle but does not publish them
(`services.swingset.dryRun = true`) until a release can restore the identity
links, registry points and events that the 10:26 UTC release `138352e`
dropped. Collection, parsing, projection and linking keep running. Publication
resumes with the release that carries the repair in
[the data quality plan](../../docs/plans/data-quality.md), and that release
gets the independent check the publish tier requires.

Two smaller choices ride along in the same switch. A newsletter finding
without a capture timestamp now blocks only the issue's year and the next
one, read from its upload path, instead of every year from 2016. Two more
phase-one findings are dismissed after review.

## Why

The 10:26 release selected a history generation that made every populated
link generation incompatible, published with zero identity links and 435
fewer events, and set a null identity baseline that the accuracy policy now
treats as the truth: every restored id counts as an expansion withheld
pending H17. Nothing in publication compares coverage with the prior release,
so each later automatic release would repeat the regression. The backlog
deferral that had spaced builds six hours apart expired at 16:26 UTC, one
cycle before this hold. A dataset that is not strictly better must not ship
(AGENTS.md, the goal).

The year bound is a proportionality fix: two undated 2022 newsletter sidebars
were keeping all of 2016 to 2026 unacceptable. The retained PDFs date each
issue in its URL, and a sidebar lists the coming months, never earlier years.

## Links

- [Investigation](../investigations/2026/published-field-gaps-2026-09-21.md)
- [Receipt](../evidence/runtime/candidate-013-2026-09-21/receipt.json)
