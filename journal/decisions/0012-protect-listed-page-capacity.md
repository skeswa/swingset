# D-0012: Reserve new-work capacity for listed result pages

Recorded: 2026-09-16  
Decided by: agent  
Topic: Scheduling  
Supersedes: —  
Superseded by: —

## Decision

After the existing scheduler chooses an eligible host and the new-work class,
divide its requests between listed result pages and discovery. Within the
selected side, keep the durable event rotation from D-0010. Start with an
unmeasured 50 percent target for listed results, configurable from one through
99 percent. This divides existing capacity; it adds no host requests and does
not reclassify current, old, or identity work.

Use declared request membership plus an explicit parser-purpose map. A member
whose parser reads event indexes is discovery, even though its URL was listed.
A recognized result parser without declared membership remains outside the
protected side. Essential platform discovery stays eligible alongside event-index
expansion. Unknown purposes remain visible as other discovery work.

Keep durable weighted credit for each selected host. When both sides are
eligible, each issued listed-result request subtracts the unreserved percentage,
and each discovery request adds the reserved percentage. Select listed results
when credit is nonnegative, otherwise discovery. When only one side is eligible,
it borrows capacity without changing credit or accumulating a future debt.
Restarts and policy changes preserve credit.

Record one purpose and one side for each scheduler-selected new-work request,
along with the captured policy and before/after credit. Commit this receipt with
the existing host and event-turn debit. Robots requests, redirects, failures,
and retries keep the selected side; the existing host ledger still charges the
actual host. A shared page receives one debit regardless of how many event
enumerations its evidence supports.

## Why

Event rotation alone does not reserve a share for acquiring known results while
new indexes continue arriving. Watch kind and enumeration membership are not
enough to distinguish the purposes: EEPro event directories use `kind=autoindex`,
scoring.dance event indexes can be listed members, and WDR can return results
directly from an event request.

Lifetime served counts would punish a side for borrowing unused capacity.
Updating credit only while both sides compete avoids that catch-up debt.
Charging actual issued requests accounts for the cost of redirects and retries.

## Consequences

One already selected fetch still finishes its bounded request chain. Credit can
temporarily reflect up to that 80-request chain's cost before the other side
receives service. The existing per-request gates can stop it sooner. This is a
weighted service target, not an exact percentage after every individual fetch.

The existing new-work class covers requests before their first retained or
checked response. Later retries keep their existing current/old classification.
This change does not implement the unfinished-event watermark, verification
caches, eligible-age alarms, or published source-event coverage. Shadow
calibration remains necessary before operating acceptance.

## Links

- [Event turn choice](0010-bounded-event-turns.md)
- [Event completion contract](../../docs/reference/scheduling.md#selection-and-protected-capacity)
- [Protected-capacity tests](../../tests/test_event_capacity.py)
