# Why no dataset iteration shipped after 2026-09-16

Reviewed on 2026-09-20 at the owner's request. The question was why days of
agent work had not produced a new dataset release. Read-only checks of the
repository, the worker and the agent session history answered it.

## Findings

**Collection was switched off the whole time.** `/var/lib/swingset/operator-hold`
was created on 2026-09-13 at 04:17 UTC. The cycle timer fired every 15 minutes
and systemd skipped it each time. The last real collection cycle ran on
2026-09-13 at 04:15 UTC. The public dataset received 12 automatic publications
between 2026-09-12 16:25 and 2026-09-13 08:45 UTC, one manual release on
2026-09-16 at 22:37 UTC (the H16 baseline `2a6c7dc`), and nothing after. The
owner asked on 2026-09-16 to remove the hold once H16 completed. Later handoff
prompts restated the hold as a "runtime interlock" and told successors not to
remove it before production input acceptance; that acceptance passed on
2026-09-18 at 01:39 UTC and no session returned to the hold.

**The goal had no finish line that produced a dataset.** "Continue until the
v2 plan is fulfilled" points at stages the plan itself sizes at 8 to 16
weekends, and the next release was gated on "operating acceptance", which no
document defined.

**Side quests displaced release work.** In order: a Dokploy and PostgreSQL
plan, a documentation reorganization with a rule to record every decision, a
link-module refactor, a personal history page and benchmark, a storage-size
question that became a multi-stage plan, a large-file history scrub, a test
suite reduction, and process rules. The storage work alone produced 41 of 174
decision records and 7,942 uncommitted lines of benchmark tooling to save 2.9
percent of a 5 GB database on a disk with 443 GB free. The disk exhaustion on
2026-09-18 came from 238 GB of rehearsal scratch, not from the database.

**Ceremony compounded.** Each operation went proposal, independent review,
seal, disposable rehearsal, production run, independent audit, receipt,
decision record, status update. Counts on 2026-09-20: 120 decision records
written on 2026-09-17 alone; 117 investigations; 1,973 evidence files, about
500 MB; about 35,000 lines of one-off tool scripts against about 51,000 lines
of product source; a 434-line status page. The active session's 120 tool calls
were all spawn, wait or message calls to reviewer subagents.

**Many sessions ran at once.** Thirty-one agent sessions started in this
checkout on 2026-09-17. Decision numbers collided and handoffs described stale
states.

**A latent service fault.** The worker's package sync step could not reinstall
the package because two entries under `/var/lib/swingset/uv-cache/archive-v0`
were root-owned since 2026-09-17 22:44 UTC, and the kernel's protected
hardlinks setting refuses the link. The first post-hold cycle would have
failed. The reviewer's own first diagnostic run triggered the uninstall half of
that step; ownership was corrected and the sync rerun before the hold was
lifted. See [D-0176](../../decisions/0176-resume-automatic-collection-and-give-the-hold-an-owner-and-expiry.md).

## Outcome

The process rules were replaced in [D-0175](../../decisions/0175-fewer-records-receipts-not-bundles-and-tiered-review.md)
and the hold was lifted in D-0176. The old status page is archived at
[status-2026-09-18](../../archive/status-2026-09-18.md).
