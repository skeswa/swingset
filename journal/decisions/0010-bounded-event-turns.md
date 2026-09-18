# D-0010: Rotate source events after a bounded request turn

Recorded: 2026-09-16  
Decided by: agent  
Topic: Scheduling  
Supersedes: —  
Superseded by: —

## Decision

Keep the existing eligible host and work-class selection. Within that selected
group, use a durable queue of source events identified by `(source, source_ref)`.
New eligible events join behind waiting events. A blocked event keeps its
position and remaining turn while independent eligible work proceeds. Indexes
and other watches without admitted enumeration membership receive their own
queue positions, so they cannot bypass event rotation.

Use an initial target of four issued HTTP requests per turn, configurable from
one through 64. These values are unmeasured defaults for shadow testing. Preserve
each turn's captured policy until the turn finishes. A changed enumeration or
canonical alias does not replace the event's queue identity. Restarting or
changing policy does not refund issued usage.

Allow an already selected fetch to finish its redirect, retry, and robots
chain. The present fetch implementation has at most 80 issued requests in one
chain. A turn therefore uses at most `target + 79` requests: 83 with the default
target. Rotate before the next selection after the target is reached. Existing
host, history, pause, deadline, and backpressure gates still run before every
request and can stop the chain sooner.

Each request in a scheduler-selected fetch receives one event owner in the existing durable host-debit
transaction. Turn usage belongs to the selected host/class queue; the existing
request ledger independently identifies the actual host reached by a redirect
or robots request. A cross-host continuation does not invent a second event
debit. Shared evidence can advance several enumerations while its request is
charged once. Record the selected enumeration, policy digest, turn position,
and reason beside the existing request receipt.

## Why

Sorting by a zero service count lets continuous new arrivals displace events
that have already received one turn. Durable tail admission instead bounds an
event's wait by the owners already ahead of it, under sufficient eligible
host/class capacity. Retrying an entire redirect chain after a hard mid-chain
cutoff can repeatedly fetch its first page and never reach its result. Finishing
the existing bounded fetch avoids that failure without adding a fetching loop
or increasing host limits.

## Consequences

Fake-clock tests cover continuous arrivals, large events, restarts, shared
requests, rollback, pauses, and the full 80-request chain. The conservative
83-request default bound includes every retry and robots redirect; it is not a
promise of four wire requests per turn. Shadow calibration and measured service
under ordinary budgets remain required before operating acceptance.

A subsequent [demand-shape rehearsal](../investigations/2026/event-completion-2026-09-16.md#retained-demand-observation)
uses 218 retained event sizes with synthetic membership and successful requests.
It demonstrates finite service at the initial settings while discovery remains
eligible. Its assumptions do not establish an operating service objective.

This increment does not implement protected acquisition capacity, overload
watermarks, eligible-age alarms, or published source-event coverage. It does
not change the frozen H16 release or clear production holds.

## Links

- [Event completion contract](../../docs/reference/scheduling.md#event-completion)
- [Extension rollout](../../docs/plans/recovery/README.md#event-completion-extension)
- [Rotation and debit tests](../../tests/test_event_turns.py)
