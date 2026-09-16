# Isolating work and sharing time fairly

A failing task should not stop unrelated progress. These rules bound individual attempts and divide time between collection and local work.

[Overview](../recovery.md) · [Current status](../../status.md)

## 4. Work: isolation and fair progress

Keep the single-writer SQLite transaction model. Separate collection admission
from derivation backlog: each cycle budgets time for reconciliation,
acquisition, and offline work. A broken parse must not indefinitely prevent
unrelated fetches. Use backpressure to bound archived-but-unprocessed bytes and
work counts, while reserving a small acquisition allowance for repairs needed
to unblock work.

Within each host's existing polite request and byte limits, reserve shares for
newly discovered pages, current-event refresh, identity confirmation, and old
evidence refresh. Allow unused shares to be borrowed. Add age promotion and a
maximum service-gap objective for each eligible kind. Set shares from measured
load in shadow operation; do not raise host limits to compensate for wasted
work. Global cycle time also needs fair allocation across hosts and kinds.

Class fairness alone does not establish event completion. Within a host and
class, give source events bounded, durable turns and protect acquisition of
already-listed result pages from discretionary index expansion. New discoveries
cannot repeatedly displace waiting events. Group by source reference before
canonical mapping; a name mismatch cannot block otherwise eligible collection.

Pin each event enumeration to its admitted parent evidence. Derive progress
from acquired artifacts, interpretations, identity decisions, and acknowledged
release support; account for unavailable and unsupported pages separately.
Retain pending children when a parent is archived or unchanged. Failure makes
the affected page wait while independent work proceeds. Report event service
and successful progress separately, with blockers and wall versus eligible age.
[Scheduling](../scheduling.md#event-completion) owns the accepted H14 extension;
its new guarantees remain pending implementation and operating acceptance.

Date-less pages receive a bounded metadata-recovery policy, not indefinite live
event polling. Gone or unpublished pages move to infrequent rechecks or an
explicit `unavailable` state with a future trigger. New parent links can
reactivate them.

Transient errors use bounded exponential backoff and host cooldowns. Repeated
deterministic parser failures under identical bytes and recipe stop immediate
retries, retain their requirement, and wait for a relevant change or review. An
unrecoverable work item is isolated with its evidence and failure reason;
unrelated scopes continue. Artifact recovery tries a verified local or backup
copy before refetching. A historical artifact without a recoverable copy is
explicitly unavailable; current source bytes cannot impersonate its historical
contents.

New-number discovery remains continuous throughout the year. Keep intensive
post-event confirmation and slower ongoing reconciliation after the intensive
window. Check explicit source IDs directly. Do not interpret 20 frontier misses
as proof that no larger number exists: revisit the frontier, recheck holes on a
bounded schedule, and use supported enumeration or verified higher-ID evidence
when available. Coverage states the searched range and time.
